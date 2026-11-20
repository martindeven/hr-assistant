"""Acceso por clave de cliente, límite de peticiones y permiso de métricas.

Ninguna función de este módulo escribe una clave en el log, en una traza ni en
un mensaje de error: los errores dicen el número de entrada y su largo.
"""

import hmac

from fastapi.responses import JSONResponse

from app.errors import ConfigError
from app.scopes import METRICS_PERMISSION, PERMISSIONS

API_KEY_HEADER = "X-API-Key"
ENTRY_SEPARATOR = ","
CLIENT_SEPARATOR = ":"
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/health",
        "/ready",
        "/static/app.js",
        "/static/app.css",
        "/docs",
        "/docs/oauth2-redirect",
        "/openapi.json",
    }
)
UNAUTHORIZED_STATUS = 401
UNAUTHORIZED_DETAIL = "invalid or missing API key"
RATE_LIMITED_STATUS = 429
RATE_LIMITED_DETAIL = "rate limit exceeded"
RETRY_AFTER_HEADER = "Retry-After"
KEY_ENCODING = "utf-8"
FORBIDDEN_STATUS = 403
FORBIDDEN_DETAIL = "forbidden for this client"
METRICS_PATH = "/metrics"
PATH_LIMIT = 200


def _malformed(number, entry):
    """Crea el error de una entrada mal formada sin mostrar su valor.

    :param number: Número de la entrada, desde 1
    :type number: int
    :param entry: Entrada sin espacios alrededor (solo se usa su largo)
    :type entry: str
    :return: El error listo para lanzar
    :rtype: app.config.ConfigError
    """
    return ConfigError(f"API_KEYS entry {number} is malformed (length {len(entry)})")


def _split_entry(number, entry):
    """Separa una entrada ``cliente:clave`` en su primer ``:``.

    :param number: Número de la entrada, desde 1
    :type number: int
    :param entry: Entrada sin espacios alrededor
    :type entry: str
    :return: El cliente y la clave
    :rtype: tuple[str, str]
    :raises app.config.ConfigError: Si falta ``:``, el cliente o la clave
    """
    client, separator, key = entry.partition(CLIENT_SEPARATOR)
    client, key = client.strip(), key.strip()
    if not separator or not client or not key:
        raise _malformed(number, entry)
    return client, key


def parse_api_keys(raw):
    """Lee ``API_KEYS`` con la forma ``cliente:clave,cliente2:clave2``.

    Vacía o con solo espacios da un diccionario vacío. Los errores se lanzan sin
    encadenar otra excepción, para que ningún traceback lleve una clave.

    :param raw: Valor de ``API_KEYS``
    :type raw: str or None
    :return: La clave de cada cliente
    :rtype: dict[str, str]
    :raises app.config.ConfigError: Si una entrada está vacía, no tiene ``:``,
        le falta el cliente o la clave, o repite un cliente o una clave
    """
    text = (raw or "").strip()
    if not text:
        return {}
    keys = {}
    seen_keys = set()
    for number, piece in enumerate(text.split(ENTRY_SEPARATOR), start=1):
        entry = piece.strip()
        if not entry:
            raise _malformed(number, entry)
        client, key = _split_entry(number, entry)
        if client in keys or key in seen_keys:
            raise _malformed(number, entry)
        keys[client] = key
        seen_keys.add(key)
    return keys


class ApiKeyAuthenticator:
    """Identifica al cliente a partir de su clave.

    Compara con ``hmac.compare_digest`` contra todas las claves, sin salir
    antes, para que el tiempo de respuesta no diga cuál se parece.

    :param keys: La clave de cada cliente
    :type keys: dict[str, str]
    """

    def __init__(self, keys):
        self._keys = [
            (client, key.encode(KEY_ENCODING)) for client, key in keys.items()
        ]

    def __repr__(self):
        """Representa el autenticador sin mostrar ninguna clave.

        :return: Cuántos clientes tiene
        :rtype: str
        """
        return f"ApiKeyAuthenticator(clients={len(self._keys)})"

    def identify(self, candidate):
        """Busca el cliente dueño de la clave.

        :param candidate: Clave que mandó la petición, o ``None``
        :type candidate: str or None
        :return: El nombre del cliente, o ``None`` si la clave no sirve
        :rtype: str or None
        """
        if not candidate:
            return None
        candidate_bytes = candidate.encode(KEY_ENCODING)
        match = None
        for client, key in self._keys:
            if hmac.compare_digest(candidate_bytes, key) and match is None:
                match = client
        return match


def _unauthorized():
    """Arma la respuesta ``401`` con el cuerpo exacto de la especificación.

    :return: La respuesta ``401``
    :rtype: fastapi.responses.JSONResponse
    """
    return JSONResponse(
        status_code=UNAUTHORIZED_STATUS, content={"detail": UNAUTHORIZED_DETAIL}
    )


def _rate_limited(retry_after):
    """Arma la respuesta ``429`` con ``Retry-After``.

    :param retry_after: Segundos que faltan para que se libere un lugar
    :type retry_after: int
    :return: La respuesta ``429``
    :rtype: fastapi.responses.JSONResponse
    """
    return JSONResponse(
        status_code=RATE_LIMITED_STATUS,
        content={"detail": RATE_LIMITED_DETAIL},
        headers={RETRY_AFTER_HEADER: str(retry_after)},
    )


def _forbidden(request_id):
    """Arma la respuesta ``403`` de una ruta que el cliente no tiene permitida.

    :param request_id: Id de la petición, o ``None``
    :type request_id: str or None
    :return: La respuesta ``403``
    :rtype: fastapi.responses.JSONResponse
    """
    return JSONResponse(
        status_code=FORBIDDEN_STATUS,
        content={"detail": FORBIDDEN_DETAIL, "request_id": request_id},
    )


def is_metrics_path(path):
    """Indica si una ruta es ``/metrics`` o cuelga de ella.

    :param path: Ruta de la petición
    :type path: str
    :return: ``True`` para ``/metrics`` y ``/metrics/...``
    :rtype: bool
    """
    return path == METRICS_PATH or path.startswith(METRICS_PATH + "/")


def _all_permissions(client):
    """Permisos por omisión: todos (para instalar la seguridad sin scopes).

    :param client: Nombre del cliente
    :type client: str
    :return: Todos los permisos
    :rtype: frozenset[str]
    """
    del client
    return PERMISSIONS


def _no_audit(action, outcome, request_id, client, **fields):
    """Auditor que no hace nada, para instalar la seguridad sin log."""
    del action, outcome, request_id, client, fields


def install_security(
    application, authenticator, limiter, *, auditor=None, permissions=None
):
    """Instala el middleware que pide clave, aplica el límite y los permisos.

    Niega por omisión: toda ruta fuera de ``PUBLIC_PATHS`` pide clave, también
    las que no existen. La clave se revisa antes de validar el cuerpo, así que
    sin clave la respuesta es ``401`` y nunca ``422``. Debe instalarse antes que
    la observabilidad para que esta quede por fuera y el ``401``/``429`` lleve
    ``X-Request-ID`` y su evento ``request``. Audita ``auth_failed`` (con
    ``client`` nulo y la ruta), ``rate_limited`` y, si el cliente no tiene el
    permiso ``metrics``, ``forbidden`` con ``403`` en ``/metrics``.

    :param application: Aplicación donde se instala
    :type application: fastapi.FastAPI
    :param authenticator: Autenticador de claves
    :type authenticator: ApiKeyAuthenticator
    :param limiter: Límite de peticiones por cliente
    :type limiter: app.ratelimit.RateLimiter
    :param auditor: Eventos ``audit``; por omisión no se audita
    :type auditor: app.audit.Auditor or None
    :param permissions: Función que da los permisos de un cliente; por omisión
        todos
    :type permissions: collections.abc.Callable[[str], frozenset[str]] or None
    """
    emit = auditor.emit if auditor is not None else _no_audit
    permissions = permissions or _all_permissions

    @application.middleware("http")
    async def protect(request, call_next):
        """Deja pasar las rutas públicas y exige clave en las demás."""
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)
        request_id = getattr(request.state, "request_id", None)
        client = authenticator.identify(request.headers.get(API_KEY_HEADER))
        if client is None:
            emit("auth_failed", "denied", request_id, None, path=path[:PATH_LIMIT])
            return _unauthorized()
        request.state.client = client
        decision = limiter.check(client)
        if not decision.allowed:
            emit("rate_limited", "denied", request_id, client)
            return _rate_limited(decision.retry_after)
        if is_metrics_path(path) and METRICS_PERMISSION not in permissions(client):
            emit("forbidden", "denied", request_id, client, path=path[:PATH_LIMIT])
            return _forbidden(request_id)
        return await call_next(request)
