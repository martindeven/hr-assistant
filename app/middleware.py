"""Middleware HTTP de observabilidad: id de petición, latencia, errores y log."""

import re

from fastapi.responses import JSONResponse

from app.security import is_metrics_path

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
METRICS_PATH = "/metrics"
ASK_PATH = "/ask"
INTERNAL_ERROR_STATUS = 500
INTERNAL_ERROR_DETAIL = "internal error"
LATENCY_DECIMALS = 2
MILLISECONDS_PER_SECOND = 1000


def resolve_request_id(header_value, new_id):
    """Elige el id de la petición: el del cliente si es seguro, o uno nuevo.

    Se acepta el del cliente si tiene de 1 a 128 caracteres de
    ``[A-Za-z0-9._-]``; así no entran saltos de línea ni textos enormes al log.

    :param header_value: Valor del encabezado ``X-Request-ID``, o ``None``
    :type header_value: str or None
    :param new_id: Generador de ids nuevos (UUID como texto)
    :type new_id: collections.abc.Callable[[], str]
    :return: El id de la petición
    :rtype: str
    """
    if header_value is not None and REQUEST_ID_PATTERN.fullmatch(header_value):
        return header_value
    return new_id()


def install_observability(application, logger, metrics, clock, new_id):
    """Instala el middleware que observa cada petición de la aplicación.

    Resuelve el ``request_id`` y lo guarda en ``request.state``, atrapa las
    excepciones (evento ``error`` y respuesta ``500`` genérica), mide la
    latencia, alimenta las métricas (salvo ``/metrics`` y ``/metrics/...``),
    cuenta cada ``/ask`` del cliente, revisa las alertas (evento ``alert`` solo
    cuando una cambia), escribe el evento ``request`` (con el ``client`` que
    dejó la seguridad, o ``null``) y pone ``X-Request-ID`` en toda respuesta.

    :param application: Aplicación donde se instala
    :type application: fastapi.FastAPI
    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param clock: Reloj inyectado para medir la latencia
    :type clock: app.clock.Clock
    :param new_id: Generador de ids nuevos
    :type new_id: collections.abc.Callable[[], str]
    """

    @application.middleware("http")
    async def observe(request, call_next):
        """Observa una petición de principio a fin."""
        started = clock.monotonic()
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER), new_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        # Se atrapa todo a propósito: el cliente recibe un 500 genérico y el log
        # guarda solo el tipo de error, nunca su mensaje (puede traer datos).
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.emit("error", request_id, error_type=type(error).__name__)
            response = JSONResponse(
                status_code=INTERNAL_ERROR_STATUS,
                content={"detail": INTERNAL_ERROR_DETAIL, "request_id": request_id},
            )
        elapsed = clock.monotonic() - started
        latency_ms = round(elapsed * MILLISECONDS_PER_SECOND, LATENCY_DECIMALS)
        path = request.url.path
        client = getattr(request.state, "client", None)
        if not is_metrics_path(path):
            _count_client(metrics, path, client)
            metrics.record_request(latency_ms=latency_ms, status=response.status_code)
            _emit_alerts(metrics, logger, request_id)
        logger.emit(
            "request",
            request_id,
            method=request.method,
            path=path,
            status=response.status_code,
            latency_ms=latency_ms,
            client=client,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def _count_client(metrics, path, client):
    """Cuenta en ``by_client`` cada ``/ask`` con cliente (con cualquier código).

    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param path: Ruta de la petición
    :type path: str
    :param client: Nombre del cliente, o ``None``
    :type client: str or None
    """
    if path == ASK_PATH and client is not None:
        metrics.record_client_request(client)


def _emit_alerts(metrics, logger, request_id):
    """Revisa las alertas y escribe un evento ``alert`` por cada cambio.

    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param request_id: Id de la petición que disparó la revisión
    :type request_id: str
    """
    for alert, state in metrics.check_alerts():
        logger.emit("alert", request_id, alert=alert, state=state)
