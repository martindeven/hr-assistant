"""Log estructurado: una línea JSON por evento."""

import json
import threading


class JsonLogger:
    """Escribe eventos como líneas JSON con los campos base.

    Cada línea lleva ``ts`` (ISO 8601 en UTC), ``service``, ``event`` y
    ``request_id``, más los campos propios del evento. Un candado evita que dos
    hilos mezclen sus líneas.

    :param stream: Destino de las líneas (por ejemplo ``sys.stdout``)
    :type stream: typing.TextIO
    :param clock: Reloj inyectado que da la hora en UTC
    :type clock: app.clock.Clock
    :param service: Nombre del servicio (campo ``service``)
    :type service: str
    """

    def __init__(self, stream, clock, service):
        self._stream = stream
        self._clock = clock
        self._service = service
        self._lock = threading.Lock()

    def emit(self, event, request_id, **fields):
        """Escribe un evento en una sola línea JSON.

        :param event: Nombre del evento (``request``, ``agent_run``, ...)
        :type event: str
        :param request_id: Id de la petición a la que pertenece el evento
        :type request_id: str
        :param fields: Campos propios del evento
        :type fields: dict
        """
        record = {
            "ts": self._clock.now_utc().isoformat(),
            "service": self._service,
            "event": event,
            "request_id": request_id,
        }
        record.update(fields)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()
