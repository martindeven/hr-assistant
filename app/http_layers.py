"""Capas HTTP del punto 3.5: encabezados de seguridad y tope del cuerpo.

Son middlewares ASGI puros (no ``BaseHTTPMiddleware``) para poder poner
encabezados en **toda** respuesta y cortar un cuerpo sin leerlo completo.
Orden de instalación en ``create_app`` (Starlette pone por fuera el último):
tamaño, seguridad, observabilidad y encabezados. Así quedan clave (``401``),
límite (``429``) y tamaño (``413``) antes de validar el cuerpo (``422``), y los
encabezados llegan también a los ``401``, ``413`` y ``500``.
"""

import base64
import hashlib
import json
import re

PRODUCTION_ENVIRONMENT = "production"
HTTP_SCOPE = "http"
REQUEST_MESSAGE = "http.request"
RESPONSE_START_MESSAGE = "http.response.start"
CONTENT_LENGTH_HEADER = b"content-length"
TOO_LARGE_STATUS = 413
TOO_LARGE_DETAIL = "request too large"
JSON_CONTENT_TYPE = b"application/json"
ALWAYS_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
PAGE_PATH = "/"
DASHBOARD_PATH = "/dashboard"
PAGE_PATHS = frozenset({PAGE_PATH, DASHBOARD_PATH})
CSP_HEADER = b"content-security-policy"
PAGE_CSP = (
    CSP_HEADER,
    b"default-src 'self'; frame-ancestors 'none'",
)
INLINE_BLOCK = r"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>"
ASK_PATH = "/ask"
NO_STORE = (b"cache-control", b"no-store")
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class PageCspError(ValueError):
    """La página del tablero no tiene exactamente un ``<script>`` y un ``<style>``."""


def _inline_hash(html, tag):
    """Calcula el hash de CSP del único bloque en línea de una etiqueta.

    :param html: HTML de la página
    :type html: str
    :param tag: ``script`` o ``style``
    :type tag: str
    :return: El hash como ``'sha256-...'``
    :rtype: str
    :raises PageCspError: Si no hay exactamente un bloque de esa etiqueta
    """
    blocks = re.findall(INLINE_BLOCK.format(tag=tag), html, flags=re.DOTALL)
    if len(blocks) != 1:
        raise PageCspError(f"dashboard.html must have exactly one <{tag}> block")
    digest = hashlib.sha256(blocks[0].encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def build_page_csp(dashboard_html):
    """Arma la CSP de ``/`` y ``/dashboard`` (la misma en las dos).

    Solo permite el código en línea del tablero por su hash: nunca
    ``'unsafe-inline'``. Se calcula al arrancar.

    :param dashboard_html: HTML de ``static/dashboard.html``
    :type dashboard_html: str
    :return: El valor del encabezado ``Content-Security-Policy``
    :rtype: str
    :raises PageCspError: Si el HTML no tiene exactamente un ``<script>`` y un
        ``<style>``
    """
    script_hash = _inline_hash(dashboard_html, "script")
    style_hash = _inline_hash(dashboard_html, "style")
    return (
        f"default-src 'self'; script-src 'self' {script_hash}; "
        f"style-src 'self' {style_hash}; frame-ancestors 'none'"
    )


def _declared_length(scope):
    """Lee ``Content-Length`` de la petición.

    :param scope: Scope ASGI de la petición
    :type scope: dict
    :return: El largo declarado, o ``None`` si no viene o no es un entero
    :rtype: int or None
    """
    for name, value in scope.get("headers", ()):
        if name.lower() == CONTENT_LENGTH_HEADER:
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_too_large(send):
    """Responde ``413 {"detail": "request too large"}``.

    :param send: Función ``send`` de ASGI
    :type send: collections.abc.Callable
    """
    body = json.dumps({"detail": TOO_LARGE_DETAIL}).encode("utf-8")
    await send(
        {
            "type": RESPONSE_START_MESSAGE,
            "status": TOO_LARGE_STATUS,
            "headers": [
                (b"content-type", JSON_CONTENT_TYPE),
                (CONTENT_LENGTH_HEADER, str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    """Corta los cuerpos que pasan de ``MAX_BODY_BYTES`` con ``413``.

    Con ``Content-Length`` mayor que el tope responde sin leer nada. Sin
    ``Content-Length`` (por ejemplo, ``chunked``) va contando lo leído y corta
    en cuanto pasa el tope; lo leído hasta ahí se entrega a la aplicación.

    :param app: Aplicación ASGI que envuelve
    :type app: collections.abc.Callable
    :param max_body_bytes: Tamaño máximo del cuerpo
    :type max_body_bytes: int
    """

    def __init__(self, app, max_body_bytes):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        """Revisa el tamaño del cuerpo antes de pasar la petición."""
        if scope["type"] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None:
            if declared > self.max_body_bytes:
                await _send_too_large(send)
                return
            await self.app(scope, receive, send)
            return
        buffered = await self._read_limited(receive)
        if buffered is None:
            await _send_too_large(send)
            return
        await self.app(scope, _replay(buffered, receive), send)

    async def _read_limited(self, receive):
        """Lee el cuerpo por partes hasta el final o hasta pasar el tope.

        :param receive: Función ``receive`` de ASGI
        :type receive: collections.abc.Callable
        :return: Los mensajes leídos, o ``None`` si el cuerpo pasa del tope
        :rtype: list[dict] or None
        """
        messages = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != REQUEST_MESSAGE:
                return messages
            total += len(message.get("body", b""))
            if total > self.max_body_bytes:
                return None
            if not message.get("more_body", False):
                return messages


def _replay(messages, receive):
    """Crea un ``receive`` que entrega primero los mensajes ya leídos.

    :param messages: Mensajes leídos
    :type messages: list[dict]
    :param receive: ``receive`` original, para lo que llegue después
    :type receive: collections.abc.Callable
    :return: El ``receive`` nuevo
    :rtype: collections.abc.Callable
    """
    pending = list(messages)

    async def replay():
        """Entrega lo leído y después delega en el ``receive`` original."""
        if pending:
            return pending.pop(0)
        return await receive()

    return replay


class SecurityHeadersMiddleware:
    """Pone los encabezados de seguridad en toda respuesta.

    Siempre ``nosniff``, ``DENY`` y ``no-referrer``; ``GET /`` y
    ``GET /dashboard`` llevan además la misma CSP; ``/ask`` lleva
    ``Cache-Control: no-store`` (también en sus ``401``, ``413`` y ``429``); en
    ``production`` toda respuesta lleva HSTS.

    :param app: Aplicación ASGI que envuelve
    :type app: collections.abc.Callable
    :param environment: ``development`` o ``production``
    :type environment: str
    :param page_csp: CSP de las páginas; por omisión la de la sesión 4
    :type page_csp: str or None
    """

    def __init__(self, app, environment, page_csp=None):
        self.app = app
        self.production = environment == PRODUCTION_ENVIRONMENT
        self.page_csp = PAGE_CSP
        if page_csp is not None:
            self.page_csp = (CSP_HEADER, page_csp.encode("ascii"))

    def _extra_headers(self, scope):
        """Encabezados de una petición según su ruta y el ambiente.

        :param scope: Scope ASGI de la petición
        :type scope: dict
        :return: Pares ``(nombre, valor)`` en bytes y en minúsculas
        :rtype: list[tuple[bytes, bytes]]
        """
        headers = list(ALWAYS_HEADERS)
        path = scope.get("path", "")
        if path in PAGE_PATHS and scope.get("method") == "GET":
            headers.append(self.page_csp)
        if path == ASK_PATH:
            headers.append(NO_STORE)
        if self.production:
            headers.append(HSTS)
        return headers

    async def __call__(self, scope, receive, send):
        """Agrega los encabezados que falten al inicio de la respuesta."""
        if scope["type"] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return
        extra = self._extra_headers(scope)

        async def send_with_headers(message):
            """Agrega los encabezados sin duplicar los que ya puso la app."""
            if message["type"] == RESPONSE_START_MESSAGE:
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(
                    (name, value) for name, value in extra if name not in present
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def install_body_limit(application, max_body_bytes):
    """Instala el tope del cuerpo (va dentro de la seguridad).

    :param application: Aplicación donde se instala
    :type application: fastapi.FastAPI
    :param max_body_bytes: Tamaño máximo del cuerpo (``MAX_BODY_BYTES``)
    :type max_body_bytes: int
    """
    application.add_middleware(BodyLimitMiddleware, max_body_bytes=max_body_bytes)


def install_security_headers(application, environment, page_csp=None):
    """Instala los encabezados de seguridad (debe ser lo último en instalarse).

    :param application: Aplicación donde se instala
    :type application: fastapi.FastAPI
    :param environment: ``development`` o ``production``
    :type environment: str
    :param page_csp: CSP de ``/`` y ``/dashboard`` (``build_page_csp``); por
        omisión la de la sesión 4
    :type page_csp: str or None
    """
    application.add_middleware(
        SecurityHeadersMiddleware, environment=environment, page_csp=page_csp
    )
