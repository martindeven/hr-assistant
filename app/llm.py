"""Interfaz del modelo de lenguaje, modelo simulado y modelo real (AI Gateway)."""

import json
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import ConfigError

TOOL_RESULT_PREFIX = "Resultado de la herramienta: "
HELP_ANSWER = "Pregúntame por un empleado (EMP-0001) o un departamento."
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


class ModelUnavailableError(RuntimeError):
    """El modelo no respondió bien: red, tiempo de espera, estado o cuerpo.

    El mensaje nunca lleva el cuerpo de la respuesta ni el token.
    """


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


class FakeModel:
    """Modelo simulado: decide con reglas fijas sobre el último mensaje.

    Responde igual con cualquier nombre: el nombre solo sirve para cobrar con
    el precio de ese modelo.

    :param departments: Departamentos que existen en los datos
    :type departments: list[str]
    :param name: Nombre del modelo (el de ``MODEL_SMALL`` o ``MODEL_LARGE``)
    :type name: str
    """

    def __init__(self, departments, name=FAKE_MODEL_NAME):
        self._departments = tuple(departments)
        self.name = name

    def complete(self, messages):
        """Aplica las 4 reglas de la especificación al último mensaje.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: La respuesta en JSON y los tokens aproximados
        :rtype: Completion
        """
        last = str(messages[-1]["content"]) if messages else ""
        content = json.dumps(self._decide(last), ensure_ascii=False)
        input_tokens = sum(_count_words(message["content"]) for message in messages)
        return Completion(
            content=content,
            input_tokens=input_tokens,
            output_tokens=_count_words(content),
        )

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
    """

    def __init__(self, name, settings, http_client=None):
        self.name = name
        self._settings = settings
        self._client = http_client or httpx.Client()
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

    def complete(self, messages):
        """Manda la conversación al gateway y devuelve su respuesta.

        :param messages: Mensajes con ``role`` y ``content``
        :type messages: list[dict]
        :return: El texto sin cercas, los tokens de ``usage`` y si fue cacheada
        :rtype: Completion
        :raises ModelUnavailableError: Si hay error de red, tiempo de espera,
            estado que no es 2xx o el cuerpo no trae los campos esperados
        """
        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
            + [
                {"role": message["role"], "content": message["content"]}
                for message in messages
            ],
            "max_tokens": self._settings.model_max_tokens,
            "temperature": self._settings.model_temperature,
        }
        try:
            response = self._client.post(
                self._url,
                json=body,
                headers=self._headers(),
                timeout=GATEWAY_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise ModelUnavailableError(
                f"gateway call failed: {type(error).__name__}"
            ) from error
        if not response.is_success:
            raise ModelUnavailableError(
                f"gateway returned status {response.status_code}"
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


def build_models(settings, directory, http_client=None):
    """Crea un modelo por nombre: el de ``MODEL_SMALL`` y el de ``MODEL_LARGE``.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param directory: Directorio de empleados (da la lista de departamentos)
    :type directory: app.directory.EmployeeDirectory
    :param http_client: Cliente HTTP para ``gateway``; por omisión uno nuevo
    :type http_client: httpx.Client or None
    :return: Los modelos por nombre
    :rtype: dict[str, LanguageModel]
    :raises ConfigError: Si el proveedor no es ``fake`` ni ``gateway``
    """
    names = (settings.model_small, settings.model_large)
    if settings.model_provider == "fake":
        departments = directory.departments()
        return {name: FakeModel(departments, name=name) for name in names}
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
