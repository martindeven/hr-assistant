"""Interfaz del modelo de lenguaje, modelo simulado y modelo real (AI Gateway)."""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from app.config import ConfigError
from app.policies import PolicyBook

TOOL_RESULT_PREFIX = "Resultado de la herramienta: "
HELP_ANSWER = "Pregúntame por un empleado (EMP-0001) o un departamento."
# Mensajes internos del agente: van al modelo como resultado de herramienta y
# nunca deben llegar a una persona (el reviewer los busca con estos textos).
INVALID_JSON_MESSAGE = "La respuesta del modelo no es JSON válido."
MISSING_REPLY_KEYS_MESSAGE = "La respuesta debe tener 'answer' o 'tool'."
TOOL_DENIED_TEMPLATE = (
    "La herramienta {tool!r} no está permitida para este especialista."
)
MISSING_ARGUMENT_TEMPLATE = "La herramienta {name} necesita el argumento '{argument}'."
INTERNAL_MESSAGE_TEMPLATES = (
    INVALID_JSON_MESSAGE,
    MISSING_REPLY_KEYS_MESSAGE,
    TOOL_DENIED_TEMPLATE,
    MISSING_ARGUMENT_TEMPLATE,
    TOOL_RESULT_PREFIX,
)
EMPLOYEE_ID_PATTERN = re.compile(r"EMP-\d{4}")
FAKE_MODEL_NAME = "fake"
GATEWAY_URL_TEMPLATE = (
    "https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}"
    "/workers-ai/v1/chat/completions"
)
GATEWAY_TIMEOUT_SECONDS = 30.0
CACHE_TTL_HEADER = "cf-aig-cache-ttl"
CACHE_STATUS_HEADER = "cf-aig-cache-status"
CACHE_HIT = "hit"
CODE_FENCE_PATTERN = re.compile(r"^```[A-Za-z]*\s*(.*?)\s*```$", re.DOTALL)
SYSTEM_PROMPT = (
    "Eres el asistente de Recursos Humanos. Responde siempre con un solo objeto "
    "JSON, sin texto alrededor. Para responder: "
    '{"answer": "<respuesta en español>"}. Para consultar el directorio usa una '
    "de estas dos herramientas: "
    '{"tool": "get_employee", "args": {"employee_id": "EMP-0001"}} o '
    '{"tool": "search_by_department", "args": {"department": "<departamento>"}}. '
    f"El resultado de una herramienta llega en un mensaje que empieza con "
    f"'{TOOL_RESULT_PREFIX}'. Responde solo con datos del directorio."
)
POLICIES_SYSTEM_PROMPT = (
    "Eres el especialista en políticas de Recursos Humanos. Responde siempre con "
    "un solo objeto JSON, sin texto alrededor. Para responder: "
    '{"answer": "<respuesta en español>"}. Para consultar una política usa la '
    'herramienta {"tool": "search_policy", "args": {"topic": "<tema>"}}. '
    f"El resultado de una herramienta llega en un mensaje que empieza con "
    f"'{TOOL_RESULT_PREFIX}'. Responde solo con el texto de las políticas."
)
DIRECTORY_SPECIALIST = "directory"
POLICIES_SPECIALIST = "policies"
SYSTEM_PROMPTS = {
    DIRECTORY_SPECIALIST: SYSTEM_PROMPT,
    POLICIES_SPECIALIST: POLICIES_SYSTEM_PROMPT,
}
SYSTEM_ROLE = "system"
POLICY_ANSWER_PREFIX = "Según la política: "
NO_POLICY_ANSWER = "No encontré una política de RH sobre ese tema."
RETRIABLE_STATUS = 429
SERVER_ERROR_MIN = 500
MILLISECONDS_PER_SECOND = 1000
DEFAULT_POLICIES_PATH = Path(__file__).resolve().parents[1] / "data" / "policies.json"


class ModelUnavailableError(RuntimeError):
    """El modelo no respondió bien: red, tiempo de espera, estado o cuerpo.

    El mensaje nunca lleva el cuerpo de la respuesta ni el token.

    :param message: Descripción sin datos sensibles
    :type message: str
    :param retriable: ``True`` si la falla es transitoria y vale la pena reintentar
    :type retriable: bool
    """

    def __init__(self, message, retriable=False):
        super().__init__(message)
        self.retriable = retriable


@dataclass(frozen=True)
class Completion:
    """Respuesta del modelo y su conteo de tokens.

    :param content: Texto de la respuesta (un objeto JSON del protocolo)
    :type content: str
    :param input_tokens: Tokens de entrada
    :type input_tokens: int
    :param output_tokens: Tokens de salida
    :type output_tokens: int
    :param cached: ``True`` si el gateway la sirvió de su caché (no cuesta)
    :type cached: bool
    """

    content: str
    input_tokens: int
    output_tokens: int
    cached: bool = False


class LanguageModel(Protocol):
    """Interfaz que cumple cualquier modelo que use el agente."""

    def complete(self, messages):
        """Responde a una conversación.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta del modelo
        :rtype: Completion
        """


def _count_words(text):
    """Cuenta las palabras de un texto (aproximación de tokens).

    :param text: Texto a contar
    :type text: str
    :return: Número de palabras
    :rtype: int
    """
    return len(str(text).split())


def _specialist_of(messages):
    """Reconoce qué especialista llama al modelo por su mensaje ``system``.

    Sin mensaje ``system`` (como en las sesiones 1 a 4) es ``directory``.

    :param messages: Mensajes con ``role`` y ``content``
    :type messages: list[dict]
    :return: El nombre del especialista
    :rtype: str
    """
    for message in messages:
        if message.get("role") == SYSTEM_ROLE:
            for name, prompt in SYSTEM_PROMPTS.items():
                if message.get("content") == prompt:
                    return name
    return DIRECTORY_SPECIALIST


def _policy_text(result):
    """Saca el texto de un resultado de ``search_policy`` para citarlo.

    :param result: Resultado de la herramienta, como texto JSON
    :type result: str
    :return: El texto de la política, el error o el resultado tal cual
    :rtype: str
    """
    try:
        data = json.loads(result)
    except ValueError:
        return result
    if isinstance(data, dict):
        for key in ("text", "error"):
            if isinstance(data.get(key), str):
                return data[key]
    return result


class FakeModel:
    """Modelo simulado: decide con reglas fijas sobre el último mensaje.

    Responde igual con cualquier nombre: el nombre solo sirve para cobrar con
    el precio de ese modelo. Reconoce al especialista que lo llama por el
    mensaje ``system`` y solo pide herramientas de ese especialista.

    :param departments: Departamentos que existen en los datos
    :type departments: list[str]
    :param name: Nombre del modelo (el de ``MODEL_SMALL`` o ``MODEL_LARGE``)
    :type name: str
    :param policy_book: Políticas para reconocer sus palabras clave; por
        omisión las de ``data/policies.json``
    :type policy_book: app.policies.PolicyBook or None
    """

    def __init__(self, departments, name=FAKE_MODEL_NAME, policy_book=None):
        self._departments = tuple(departments)
        self.name = name
        self._policy_book = policy_book or PolicyBook.load(DEFAULT_POLICIES_PATH)

    def complete(self, messages):
        """Aplica las reglas de la especificación al último mensaje.

        Los mensajes ``system`` no cuentan como tokens: el modelo simulado
        aproxima solo la conversación, como en las sesiones anteriores.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta en JSON y los tokens aproximados
        :rtype: Completion
        """
        last = str(messages[-1]["content"]) if messages else ""
        if _specialist_of(messages) == POLICIES_SPECIALIST:
            decision = self._decide_policy(last)
        else:
            decision = self._decide(last)
        content = json.dumps(decision, ensure_ascii=False)
        input_tokens = sum(
            _count_words(message["content"])
            for message in messages
            if message.get("role") != SYSTEM_ROLE
        )
        return Completion(
            content=content,
            input_tokens=input_tokens,
            output_tokens=_count_words(content),
        )

    def _decide_policy(self, last):
        """Elige la respuesta del especialista ``policies``.

        Con un resultado de herramienta lo cita; con una palabra clave de una
        política pide ``search_policy``; si no, dice que no hay política.

        :param last: Contenido del último mensaje
        :type last: str
        :return: Objeto del protocolo (``answer`` o ``tool``)
        :rtype: dict
        """
        if last.startswith(TOOL_RESULT_PREFIX):
            result = last[len(TOOL_RESULT_PREFIX):]
            return {"answer": POLICY_ANSWER_PREFIX + _policy_text(result)}
        topic = self._policy_book.find_keyword(last)
        if topic is not None:
            return {"tool": "search_policy", "args": {"topic": topic}}
        return {"answer": NO_POLICY_ANSWER}

    def _decide(self, last):
        """Elige la respuesta según el último mensaje.

        :param last: Contenido del último mensaje
        :type last: str
        :return: Objeto del protocolo (``answer`` o ``tool``)
        :rtype: dict
        """
        if last.startswith(TOOL_RESULT_PREFIX):
            result = last[len(TOOL_RESULT_PREFIX):]
            return {"answer": f"Según el directorio: {result}"}
        match = EMPLOYEE_ID_PATTERN.search(last)
        if match:
            return {"tool": "get_employee", "args": {"employee_id": match.group()}}
        department = self._find_department(last)
        if department is not None:
            return {
                "tool": "search_by_department",
                "args": {"department": department},
            }
        return {"answer": HELP_ANSWER}

    def _find_department(self, text):
        """Busca en el texto un departamento conocido, sin distinguir mayúsculas.

        :param text: Texto donde buscar
        :type text: str
        :return: El departamento encontrado o ``None``
        :rtype: str or None
        """
        folded = text.casefold()
        for department in self._departments:
            if department.casefold() in folded:
                return department
        return None


def _strip_code_fences(text):
    """Quita las cercas de código (por ejemplo ```json) que rodean la respuesta.

    :param text: Texto devuelto por el modelo
    :type text: str
    :return: El texto sin cercas ni espacios sobrantes
    :rtype: str
    """
    stripped = text.strip()
    match = CODE_FENCE_PATTERN.match(stripped)
    return match.group(1) if match else stripped


def _read_reply(data):
    """Saca el texto y los tokens del cuerpo de la respuesta del gateway.

    :param data: Cuerpo JSON de la respuesta
    :type data: object
    :return: El texto y los tokens de entrada y salida
    :rtype: tuple[str, int, int]
    :raises ModelUnavailableError: Si faltan campos o tienen otro tipo
    """
    try:
        content = data["choices"][0]["message"]["content"]
        usage = data["usage"]
        input_tokens = usage["prompt_tokens"]
        output_tokens = usage["completion_tokens"]
    except (KeyError, IndexError, TypeError) as error:
        raise ModelUnavailableError("gateway reply is missing fields") from error
    if not isinstance(content, str) or not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (input_tokens, output_tokens)
    ):
        raise ModelUnavailableError("gateway reply has fields of the wrong type")
    return content, input_tokens, output_tokens


class GatewayModel:
    """Modelo real de Workers AI a través de Cloudflare AI Gateway.

    Nunca registra la URL, los encabezados ni el cuerpo: llevan la cuenta y el
    token.

    :param name: Nombre del modelo de Workers AI
    :type name: str
    :param settings: Configuración con las variables ``CF_*`` y los topes
    :type settings: app.config.Settings
    :param http_client: Cliente HTTP inyectado; por omisión uno nuevo
    :type http_client: httpx.Client or None
    :param sleep: Función que espera los segundos indicados entre reintentos;
        por omisión ``time.sleep`` (las pruebas inyectan una falsa)
    :type sleep: collections.abc.Callable[[float], None] or None
    """

    def __init__(self, name, settings, http_client=None, sleep=None):
        self.name = name
        self._settings = settings
        self._client = http_client or httpx.Client()
        self._sleep = sleep or time.sleep
        self._url = GATEWAY_URL_TEMPLATE.format(
            account_id=settings.cf_account_id, gateway_id=settings.cf_gateway_id
        )

    def _headers(self):
        """Arma los encabezados: token y, si aplica, el TTL de la caché.

        :return: Los encabezados de la petición
        :rtype: dict[str, str]
        """
        headers = {"Authorization": f"Bearer {self._settings.cf_api_token}"}
        if self._settings.cache_ttl_seconds > 0:
            headers[CACHE_TTL_HEADER] = str(self._settings.cache_ttl_seconds)
        return headers

    def _body(self, messages):
        """Arma el cuerpo de la petición con un solo mensaje ``system``.

        Si la conversación ya trae el ``system`` del especialista, se usa ese;
        si no, el de ``directory``, como en las sesiones anteriores.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: El cuerpo JSON de la petición
        :rtype: dict
        """
        sent = [
            {"role": message["role"], "content": message["content"]}
            for message in messages
        ]
        if not any(message["role"] == SYSTEM_ROLE for message in sent):
            sent.insert(0, {"role": SYSTEM_ROLE, "content": SYSTEM_PROMPT})
        return {
            "model": self.name,
            "messages": sent,
            "max_tokens": self._settings.model_max_tokens,
            "temperature": self._settings.model_temperature,
        }

    def complete(self, messages):
        """Manda la conversación al gateway, con reintentos, y devuelve su respuesta.

        Hace hasta ``1 + MODEL_RETRIES`` intentos. Entre intentos espera
        ``MODEL_RETRY_BACKOFF_MS`` y duplica la espera cada vez. Solo reintenta
        fallas transitorias: tiempo de espera, red, ``429`` y ``5xx``.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: El texto sin cercas, los tokens de ``usage`` y si fue cacheada
        :rtype: Completion
        :raises ModelUnavailableError: Si hay error de red, tiempo de espera,
            estado que no es 2xx o el cuerpo no trae los campos esperados, y ya
            no quedan reintentos (o la falla no se reintenta)
        """
        body = self._body(messages)
        retries = self._settings.model_retries
        wait_seconds = self._settings.model_retry_backoff_ms / MILLISECONDS_PER_SECOND
        attempt = 0
        while True:
            try:
                return self._attempt(body)
            except ModelUnavailableError as error:
                if not error.retriable or attempt >= retries:
                    raise
            self._sleep(wait_seconds)
            wait_seconds *= 2
            attempt += 1

    def _attempt(self, body):
        """Hace una sola llamada al gateway.

        :param body: Cuerpo JSON de la petición
        :type body: dict
        :return: La respuesta del modelo
        :rtype: Completion
        :raises ModelUnavailableError: Si la llamada falla; ``retriable`` dice si
            la falla es transitoria
        """
        try:
            response = self._client.post(
                self._url,
                json=body,
                headers=self._headers(),
                timeout=self._settings.model_timeout_seconds,
            )
        except httpx.HTTPError as error:
            raise ModelUnavailableError(
                f"gateway call failed: {type(error).__name__}", retriable=True
            ) from error
        if not response.is_success:
            status = response.status_code
            raise ModelUnavailableError(
                f"gateway returned status {status}",
                retriable=status == RETRIABLE_STATUS or status >= SERVER_ERROR_MIN,
            )
        try:
            data = response.json()
        except ValueError as error:
            raise ModelUnavailableError("gateway reply is not JSON") from error
        content, input_tokens, output_tokens = _read_reply(data)
        cache_status = response.headers.get(CACHE_STATUS_HEADER, "")
        return Completion(
            content=_strip_code_fences(content),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached=cache_status.strip().lower() == CACHE_HIT,
        )


def build_models(settings, directory, http_client=None, policy_book=None):
    """Crea un modelo por nombre: el de ``MODEL_SMALL`` y el de ``MODEL_LARGE``.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param directory: Directorio de empleados (da la lista de departamentos)
    :type directory: app.directory.EmployeeDirectory
    :param http_client: Cliente HTTP para ``gateway``; por omisión uno nuevo
    :type http_client: httpx.Client or None
    :param policy_book: Políticas que reconoce el modelo simulado; por omisión
        las de ``POLICIES_PATH``
    :type policy_book: app.policies.PolicyBook or None
    :return: Los modelos por nombre
    :rtype: dict[str, LanguageModel]
    :raises ConfigError: Si el proveedor no es ``fake`` ni ``gateway``
    """
    names = (settings.model_small, settings.model_large)
    if settings.model_provider == "fake":
        departments = directory.departments()
        book = policy_book or PolicyBook.load(settings.policies_path)
        return {
            name: FakeModel(departments, name=name, policy_book=book)
            for name in names
        }
    if settings.model_provider == "gateway":
        client = http_client or httpx.Client()
        return {
            name: GatewayModel(name=name, settings=settings, http_client=client)
            for name in names
        }
    raise ConfigError(
        f"MODEL_PROVIDER {settings.model_provider!r} is not supported"
    )


def build_model(settings, directory):
    """Crea el modelo de omisión de la configuración (el de ``MODEL_LARGE``).

    Se conserva por compatibilidad con la sesión 2.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param directory: Directorio de empleados (da la lista de departamentos)
    :type directory: app.directory.EmployeeDirectory
    :return: El modelo
    :rtype: LanguageModel
    :raises ConfigError: Si el proveedor no es ``fake`` ni ``gateway``
    """
    return build_models(settings, directory)[settings.model_large]
