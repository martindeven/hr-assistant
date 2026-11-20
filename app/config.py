"""Configuración del HR Assistant, leída de un mapeo de variables de entorno."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.compliance import check_production
from app.errors import ConfigError
from app.pricing import PriceError, PriceTable
from app.scopes import parse_api_key_scopes
from app.security import parse_api_keys

__all__ = ["ConfigError", "Settings", "load_settings"]

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PROVIDERS = ("fake", "gateway")
GATEWAY_PROVIDER = "gateway"
GATEWAY_VARIABLES = ("CF_ACCOUNT_ID", "CF_GATEWAY_ID", "CF_API_TOKEN")
DEFAULT_MODEL_PROVIDER = "fake"
DEFAULT_CF_GATEWAY_ID = "default"
DEFAULT_MODEL_SMALL = "@cf/meta/llama-3.1-8b-instruct-fast"
DEFAULT_MODEL_LARGE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
DEFAULT_ROUTING = "true"
DEFAULT_ROUTER_MAX_CHARS = "80"
DEFAULT_HISTORY_WINDOW = "6"
DEFAULT_MODEL_MAX_TOKENS = "300"
DEFAULT_MODEL_TEMPERATURE = "0"
DEFAULT_CACHE_TTL_SECONDS = "0"
DEFAULT_PRICES_PATH = "data/model_prices.json"
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0
DEFAULT_AGENT_MAX_STEPS = "3"
DEFAULT_MAX_QUESTION_CHARS = "2000"
DEFAULT_MAX_HISTORY_MESSAGES = "20"
DEFAULT_MAX_HISTORY_CHARS = "4000"
DEFAULT_EMPLOYEES_PATH = "data/employees.json"
DEFAULT_SERVICE_NAME = "hr-assistant"
DEFAULT_SLO_P95_MS = "2000"
BOOLEAN_VALUES = {"true": True, "false": False}
DEFAULT_ENVIRONMENT = "development"
SUPPORTED_ENVIRONMENTS = ("development", "production")
DEFAULT_RATE_LIMIT_PER_MINUTE = "30"
DEFAULT_POLICIES_PATH = "data/policies.json"
DEFAULT_MODEL_RETRIES = "2"
DEFAULT_MODEL_RETRY_BACKOFF_MS = "200"
DEFAULT_MODEL_FALLBACK = "true"
DEFAULT_MODEL_TIMEOUT_SECONDS = "30"
DEFAULT_PII_INPUT_MODE = "redact"
PII_INPUT_MODES = ("redact", "block", "off")
DEFAULT_INJECTION_GUARD = "true"
DEFAULT_MAX_ANSWER_CHARS = "1500"
DEFAULT_MAX_BODY_BYTES = "65536"


# Es un registro plano e inmutable de variables de entorno: cada variable es un
# campo, así que pasa del límite de atributos de pylint sin agregar complejidad.
@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """Configuración inmutable de la aplicación.

    :param model_provider: Proveedor del modelo: ``fake`` o ``gateway``
    :type model_provider: str
    :param agent_max_steps: Tope de llamadas al modelo por pregunta
    :type agent_max_steps: int
    :param max_question_chars: Largo máximo de la pregunta
    :type max_question_chars: int
    :param employees_path: Ruta absoluta al archivo de empleados
    :type employees_path: pathlib.Path
    :param service_name: Nombre del servicio en el log (campo ``service``)
    :type service_name: str
    :param slo_p95_ms: Meta de latencia p95 en milisegundos
    :type slo_p95_ms: int
    :param tracing_enabled: Si se mandan trazas a LangSmith (pide la clave)
    :type tracing_enabled: bool
    :param hide_inputs: Si no se mandan a LangSmith las entradas
    :type hide_inputs: bool
    :param hide_outputs: Si no se mandan a LangSmith las salidas
    :type hide_outputs: bool
    :param max_history_messages: Máximo de mensajes en el historial de la petición
    :type max_history_messages: int
    :param max_history_chars: Largo máximo del texto de cada mensaje del historial
    :type max_history_chars: int
    :param cf_account_id: Cuenta de Cloudflare (solo con ``gateway``)
    :type cf_account_id: str
    :param cf_gateway_id: Id del AI Gateway de Cloudflare
    :type cf_gateway_id: str
    :param cf_api_token: Token de Cloudflare; es secreto y no sale en ``repr``
    :type cf_api_token: str
    :param model_small: Nombre del modelo barato
    :type model_small: str
    :param model_large: Nombre del modelo capaz
    :type model_large: str
    :param routing: Si se elige el modelo por pregunta
    :type routing: bool
    :param router_max_chars: Largo a partir del cual la pregunta va al modelo capaz
    :type router_max_chars: int
    :param history_window: Mensajes del historial que se mandan al modelo
    :type history_window: int
    :param model_max_tokens: Tope de tokens de salida por llamada
    :type model_max_tokens: int
    :param model_temperature: Temperatura del modelo, de 0 a 2
    :type model_temperature: float
    :param cache_ttl_seconds: Segundos de caché del gateway (0: apagada)
    :type cache_ttl_seconds: int
    :param prices_path: Ruta absoluta al archivo de precios
    :type prices_path: pathlib.Path
    :param environment: ``development`` o ``production`` (aplica compliance)
    :type environment: str
    :param api_keys: Clave de cada cliente; es secreto y no sale en ``repr``
    :type api_keys: dict[str, str]
    :param rate_limit_per_minute: Peticiones por cliente por minuto
    :type rate_limit_per_minute: int
    :param policies_path: Ruta absoluta al archivo de políticas de RH
    :type policies_path: pathlib.Path
    :param model_retries: Reintentos ante fallas transitorias del modelo
    :type model_retries: int
    :param model_retry_backoff_ms: Espera inicial entre reintentos (se duplica)
    :type model_retry_backoff_ms: int
    :param model_fallback: Si se responde con ``MODEL_SMALL`` cuando
        ``MODEL_LARGE`` falla
    :type model_fallback: bool
    :param model_timeout_seconds: Tope de espera de cada llamada al modelo
    :type model_timeout_seconds: float
    :param pii_input_mode: ``redact``, ``block`` u ``off``
    :type pii_input_mode: str
    :param injection_guard: Si se bloquea la inyección de instrucciones
    :type injection_guard: bool
    :param max_answer_chars: Largo máximo de la respuesta
    :type max_answer_chars: int
    :param max_body_bytes: Tamaño máximo del cuerpo de una petición
    :type max_body_bytes: int
    :param api_key_scopes: Permisos de cada cliente; nombra a los clientes, así
        que no sale en ``repr``
    :type api_key_scopes: dict[str, frozenset[str]]
    """

    model_provider: str
    agent_max_steps: int
    max_question_chars: int
    employees_path: Path
    service_name: str = DEFAULT_SERVICE_NAME
    slo_p95_ms: int = int(DEFAULT_SLO_P95_MS)
    tracing_enabled: bool = False
    hide_inputs: bool = True
    hide_outputs: bool = True
    max_history_messages: int = int(DEFAULT_MAX_HISTORY_MESSAGES)
    max_history_chars: int = int(DEFAULT_MAX_HISTORY_CHARS)
    cf_account_id: str = field(default="", repr=False)
    cf_gateway_id: str = DEFAULT_CF_GATEWAY_ID
    cf_api_token: str = field(default="", repr=False)
    model_small: str = DEFAULT_MODEL_SMALL
    model_large: str = DEFAULT_MODEL_LARGE
    routing: bool = True
    router_max_chars: int = int(DEFAULT_ROUTER_MAX_CHARS)
    history_window: int = int(DEFAULT_HISTORY_WINDOW)
    model_max_tokens: int = int(DEFAULT_MODEL_MAX_TOKENS)
    model_temperature: float = float(DEFAULT_MODEL_TEMPERATURE)
    cache_ttl_seconds: int = int(DEFAULT_CACHE_TTL_SECONDS)
    prices_path: Path = PRODUCT_ROOT / DEFAULT_PRICES_PATH
    environment: str = DEFAULT_ENVIRONMENT
    api_keys: dict = field(default_factory=dict, repr=False, compare=False)
    rate_limit_per_minute: int = int(DEFAULT_RATE_LIMIT_PER_MINUTE)
    policies_path: Path = PRODUCT_ROOT / DEFAULT_POLICIES_PATH
    model_retries: int = int(DEFAULT_MODEL_RETRIES)
    model_retry_backoff_ms: int = int(DEFAULT_MODEL_RETRY_BACKOFF_MS)
    model_fallback: bool = True
    model_timeout_seconds: float = float(DEFAULT_MODEL_TIMEOUT_SECONDS)
    pii_input_mode: str = DEFAULT_PII_INPUT_MODE
    injection_guard: bool = True
    max_answer_chars: int = int(DEFAULT_MAX_ANSWER_CHARS)
    max_body_bytes: int = int(DEFAULT_MAX_BODY_BYTES)
    api_key_scopes: dict = field(default_factory=dict, repr=False, compare=False)


def _data_path(env, name, default):
    """Resuelve la ruta de un archivo de datos y comprueba que exista.

    Las rutas relativas se resuelven contra la raíz de ``product/``.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Ruta por omisión
    :type default: str
    :return: Ruta absoluta al archivo
    :rtype: pathlib.Path
    :raises ConfigError: Si el archivo no existe
    """
    raw = env.get(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PRODUCT_ROOT / path
    if not path.is_file():
        raise ConfigError(f"{name} points to a missing file: {raw!r}")
    return path.resolve()


def _positive_number(env, name, default):
    """Lee una variable como número mayor que cero.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Valor por omisión, como texto
    :type default: str
    :return: El número leído
    :rtype: float
    :raises ConfigError: Si no es un número o no es mayor que cero
    """
    raw = env.get(name, default)
    try:
        value = float(str(raw).strip())
    except ValueError as error:
        raise ConfigError(f"{name} must be a positive number, got {raw!r}") from error
    # ``isfinite`` descarta ``nan`` e ``inf``, que ``float`` acepta.
    if not (math.isfinite(value) and value > 0):
        raise ConfigError(f"{name} must be a positive number, got {raw!r}")
    return value


def _pii_input_mode(env):
    """Lee ``PII_INPUT_MODE``: ``redact`` (omisión), ``block`` u ``off``.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: El modo
    :rtype: str
    :raises ConfigError: Si el valor no es uno de los permitidos
    """
    value = _text(env, "PII_INPUT_MODE", DEFAULT_PII_INPUT_MODE).lower()
    if value not in PII_INPUT_MODES:
        raise ConfigError(f"PII_INPUT_MODE must be one of {PII_INPUT_MODES}")
    return value


def _resilience_fields(env):
    """Lee las variables de reintentos, respaldo y tiempo de espera del modelo.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: Los campos de ``Settings`` por nombre
    :rtype: dict
    """
    return {
        "model_retries": _non_negative_int(
            env, "MODEL_RETRIES", DEFAULT_MODEL_RETRIES
        ),
        "model_retry_backoff_ms": _non_negative_int(
            env, "MODEL_RETRY_BACKOFF_MS", DEFAULT_MODEL_RETRY_BACKOFF_MS
        ),
        "model_fallback": _boolean(env, "MODEL_FALLBACK", DEFAULT_MODEL_FALLBACK),
        "model_timeout_seconds": _positive_number(
            env, "MODEL_TIMEOUT_SECONDS", DEFAULT_MODEL_TIMEOUT_SECONDS
        ),
    }


def _guardrail_fields(env):
    """Lee las variables del guard, del reviewer y del tamaño del cuerpo.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: Los campos de ``Settings`` por nombre
    :rtype: dict
    """
    return {
        "pii_input_mode": _pii_input_mode(env),
        "injection_guard": _boolean(env, "INJECTION_GUARD", DEFAULT_INJECTION_GUARD),
        "max_answer_chars": _positive_int(
            env, "MAX_ANSWER_CHARS", DEFAULT_MAX_ANSWER_CHARS
        ),
        "max_body_bytes": _positive_int(env, "MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
    }


def _non_negative_int(env, name, default):
    """Lee una variable como entero de 0 en adelante.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Valor por omisión, como texto
    :type default: str
    :return: El entero leído
    :rtype: int
    :raises ConfigError: Si el valor no es un entero mayor o igual que cero
    """
    raw = env.get(name, default)
    try:
        value = int(str(raw).strip())
    except ValueError as error:
        raise ConfigError(
            f"{name} must be a non-negative integer, got {raw!r}"
        ) from error
    if value < 0:
        raise ConfigError(f"{name} must be a non-negative integer, got {raw!r}")
    return value


def _temperature(env):
    """Lee ``MODEL_TEMPERATURE`` como decimal entre 0 y 2.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: La temperatura
    :rtype: float
    :raises ConfigError: Si no es un número o está fuera de rango
    """
    raw = env.get("MODEL_TEMPERATURE", DEFAULT_MODEL_TEMPERATURE)
    try:
        value = float(str(raw).strip())
    except ValueError as error:
        raise ConfigError(
            f"MODEL_TEMPERATURE must be a number, got {raw!r}"
        ) from error
    if not MIN_TEMPERATURE <= value <= MAX_TEMPERATURE:
        raise ConfigError(
            f"MODEL_TEMPERATURE must be between {MIN_TEMPERATURE} and "
            f"{MAX_TEMPERATURE}, got {raw!r}"
        )
    return value


def _text(env, name, default):
    """Lee una variable de texto sin espacios sobrantes.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Valor por omisión
    :type default: str
    :return: El texto leído
    :rtype: str
    """
    return str(env.get(name, default)).strip()


def _gateway_credentials(env, provider):
    """Lee las variables ``CF_*`` y exige las tres si el proveedor es ``gateway``.

    Los mensajes de error nombran la variable, nunca su valor.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param provider: Proveedor del modelo ya validado
    :type provider: str
    :return: Cuenta, gateway y token
    :rtype: tuple[str, str, str]
    :raises ConfigError: Si falta alguna con ``gateway``
    """
    defaults = {"CF_GATEWAY_ID": DEFAULT_CF_GATEWAY_ID}
    values = [_text(env, name, defaults.get(name, "")) for name in GATEWAY_VARIABLES]
    if provider == GATEWAY_PROVIDER:
        for name, value in zip(GATEWAY_VARIABLES, values):
            if not value:
                raise ConfigError(f"{name} is required with MODEL_PROVIDER=gateway")
    return values[0], values[1], values[2]


def _prices_path(env):
    """Resuelve la ruta del archivo de precios y comprueba que exista.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: Ruta absoluta al archivo
    :rtype: pathlib.Path
    :raises ConfigError: Si el archivo no existe
    """
    raw = env.get("PRICES_PATH", DEFAULT_PRICES_PATH)
    path = Path(raw)
    if not path.is_absolute():
        path = PRODUCT_ROOT / path
    if not path.is_file():
        raise ConfigError(f"PRICES_PATH points to a missing file: {raw!r}")
    return path.resolve()


def _priced_models(env, prices_path):
    """Lee ``MODEL_SMALL`` y ``MODEL_LARGE`` y comprueba que tengan precio.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param prices_path: Archivo de precios
    :type prices_path: pathlib.Path
    :return: Los nombres del modelo barato y del capaz
    :rtype: tuple[str, str]
    :raises ConfigError: Si un nombre está vacío, no tiene precio o el archivo
        de precios no es válido
    """
    try:
        table = PriceTable.load(prices_path)
    except PriceError as error:
        raise ConfigError(
            f"PRICES_PATH is not a valid price file: {error}"
        ) from error
    names = []
    for name, default in (
        ("MODEL_SMALL", DEFAULT_MODEL_SMALL),
        ("MODEL_LARGE", DEFAULT_MODEL_LARGE),
    ):
        model = _text(env, name, default)
        if not model:
            raise ConfigError(f"{name} must not be empty")
        if not table.has_price(model):
            raise ConfigError(f"{name} {model!r} has no price in PRICES_PATH")
        names.append(model)
    return names[0], names[1]


def _positive_int(env, name, default):
    """Lee una variable como entero positivo.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Valor por omisión, como texto
    :type default: str
    :return: El entero leído
    :rtype: int
    :raises ConfigError: Si el valor no es un entero mayor que cero
    """
    raw = env.get(name, default)
    try:
        value = int(str(raw).strip())
    except ValueError as error:
        raise ConfigError(
            f"{name} must be a positive integer, got {raw!r}"
        ) from error
    if value < 1:
        raise ConfigError(f"{name} must be a positive integer, got {raw!r}")
    return value


def _boolean(env, name, default):
    """Lee una variable como booleano estricto (``true`` o ``false``).

    No distingue mayúsculas de minúsculas.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :param name: Nombre de la variable
    :type name: str
    :param default: Valor por omisión, como texto
    :type default: str
    :return: El booleano leído
    :rtype: bool
    :raises ConfigError: Si el valor no es ``true`` ni ``false``
    """
    raw = env.get(name, default)
    folded = str(raw).strip().lower()
    if folded not in BOOLEAN_VALUES:
        raise ConfigError(f"{name} must be 'true' or 'false', got {raw!r}")
    return BOOLEAN_VALUES[folded]


def _service_name(env):
    """Lee el nombre del servicio y comprueba que no esté vacío.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: El nombre sin espacios sobrantes
    :rtype: str
    :raises ConfigError: Si el nombre está vacío
    """
    name = str(env.get("SERVICE_NAME", DEFAULT_SERVICE_NAME)).strip()
    if not name:
        raise ConfigError("SERVICE_NAME must not be empty")
    return name


def _tracing_enabled(env):
    """Decide si se encienden las trazas.

    Hace falta ``LANGSMITH_TRACING=true`` y que exista ``LANGSMITH_API_KEY``. La
    clave solo se comprueba: nunca se guarda ni se devuelve.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: ``True`` si las trazas quedan encendidas
    :rtype: bool
    :raises ConfigError: Si ``LANGSMITH_TRACING`` no es un booleano
    """
    requested = _boolean(env, "LANGSMITH_TRACING", "false")
    has_key = bool(str(env.get("LANGSMITH_API_KEY", "")).strip())
    return requested and has_key


def _employees_path(env):
    """Resuelve la ruta del archivo de empleados y comprueba que exista.

    Las rutas relativas se resuelven contra la raíz de ``product/``.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: Ruta absoluta al archivo
    :rtype: pathlib.Path
    :raises ConfigError: Si el archivo no existe
    """
    raw = env.get("EMPLOYEES_PATH", DEFAULT_EMPLOYEES_PATH)
    path = Path(raw)
    if not path.is_absolute():
        path = PRODUCT_ROOT / path
    if not path.is_file():
        raise ConfigError(f"EMPLOYEES_PATH points to a missing file: {raw!r}")
    return path.resolve()


def _environment(env):
    """Lee ``ENVIRONMENT``: ``development`` (omisión) o ``production``.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: El ambiente
    :rtype: str
    :raises ConfigError: Si el valor no es uno de los permitidos
    """
    value = _text(env, "ENVIRONMENT", DEFAULT_ENVIRONMENT)
    if value not in SUPPORTED_ENVIRONMENTS:
        raise ConfigError(
            f"ENVIRONMENT must be one of {SUPPORTED_ENVIRONMENTS}, got {value!r}"
        )
    return value


def load_settings(env: Mapping[str, str]) -> Settings:
    """Construye la configuración a partir de un mapeo inyectado.

    Con ``ENVIRONMENT=production`` aplica además las reglas de compliance, que
    impiden arrancar con una configuración insegura.

    :param env: Mapeo de variables de entorno (por ejemplo ``os.environ``)
    :type env: Mapping[str, str]
    :return: La configuración validada
    :rtype: Settings
    :raises ConfigError: Si alguna variable tiene un valor inválido o falla una
        regla de compliance
    """
    settings = _build_settings(env)
    check_production(settings)
    return settings


def _build_settings(env):
    """Lee y valida cada variable de entorno.

    :param env: Mapeo de variables de entorno
    :type env: Mapping[str, str]
    :return: La configuración validada, sin revisar compliance
    :rtype: Settings
    :raises ConfigError: Si alguna variable tiene un valor inválido
    """
    provider = str(env.get("MODEL_PROVIDER", DEFAULT_MODEL_PROVIDER)).strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigError(
            f"MODEL_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {provider!r}"
        )
    account_id, gateway_id, api_token = _gateway_credentials(env, provider)
    prices_path = _prices_path(env)
    model_small, model_large = _priced_models(env, prices_path)
    api_keys = parse_api_keys(env.get("API_KEYS", ""))
    return Settings(
        model_provider=provider,
        agent_max_steps=_positive_int(
            env, "AGENT_MAX_STEPS", DEFAULT_AGENT_MAX_STEPS
        ),
        max_question_chars=_positive_int(
            env, "MAX_QUESTION_CHARS", DEFAULT_MAX_QUESTION_CHARS
        ),
        employees_path=_employees_path(env),
        service_name=_service_name(env),
        slo_p95_ms=_positive_int(env, "SLO_P95_MS", DEFAULT_SLO_P95_MS),
        tracing_enabled=_tracing_enabled(env),
        hide_inputs=_boolean(env, "HIDE_INPUTS", "true"),
        hide_outputs=_boolean(env, "HIDE_OUTPUTS", "true"),
        max_history_messages=_positive_int(
            env, "MAX_HISTORY_MESSAGES", DEFAULT_MAX_HISTORY_MESSAGES
        ),
        max_history_chars=_positive_int(
            env, "MAX_HISTORY_CHARS", DEFAULT_MAX_HISTORY_CHARS
        ),
        cf_account_id=account_id,
        cf_gateway_id=gateway_id,
        cf_api_token=api_token,
        model_small=model_small,
        model_large=model_large,
        routing=_boolean(env, "ROUTING", DEFAULT_ROUTING),
        router_max_chars=_positive_int(
            env, "ROUTER_MAX_CHARS", DEFAULT_ROUTER_MAX_CHARS
        ),
        history_window=_non_negative_int(
            env, "HISTORY_WINDOW", DEFAULT_HISTORY_WINDOW
        ),
        model_max_tokens=_positive_int(
            env, "MODEL_MAX_TOKENS", DEFAULT_MODEL_MAX_TOKENS
        ),
        model_temperature=_temperature(env),
        cache_ttl_seconds=_non_negative_int(
            env, "CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS
        ),
        prices_path=prices_path,
        environment=_environment(env),
        api_keys=api_keys,
        api_key_scopes=parse_api_key_scopes(
            env.get("API_KEY_SCOPES", ""), api_keys.keys()
        ),
        rate_limit_per_minute=_positive_int(
            env, "RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE
        ),
        policies_path=_data_path(env, "POLICIES_PATH", DEFAULT_POLICIES_PATH),
        **_resilience_fields(env),
        **_guardrail_fields(env),
    )
