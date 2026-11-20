"""Eventos ``audit``: un solo lugar que los escribe y que nunca acepta texto.

Cada evento lleva ``client``, ``request_id``, ``action`` y ``outcome``. Los
campos con el texto de la pregunta, de la respuesta o una clave se rechazan
para que ningún llamador los filtre por error.
"""

from app.pii import redact

AUDIT_EVENT = "audit"
FORBIDDEN_FIELDS = frozenset(
    {"question", "answer", "text", "content", "key", "api_key", "token", "history"}
)
AUDIT_TEXT_LIMIT = 200


class AuditFieldError(ValueError):
    """Un llamador intentó auditar un campo que puede llevar texto o claves."""


def _trim(value):
    """Tapa los datos personales de un texto y lo recorta al largo del audit.

    Otros valores quedan igual.

    :param value: Valor a recortar
    :type value: object
    :return: El valor, con los textos tapados y recortados
    :rtype: object
    """
    if isinstance(value, str):
        return redact(value)[0][:AUDIT_TEXT_LIMIT]
    return value


def audit_args(args):
    """Prepara los argumentos de una herramienta para el evento ``audit``.

    Se audita lo que pidió el modelo, aunque no sea válido, con cada texto
    recortado a ``AUDIT_TEXT_LIMIT`` caracteres.

    :param args: Argumentos que pidió el modelo
    :type args: object
    :return: Los argumentos con los textos recortados
    :rtype: object
    """
    if isinstance(args, dict):
        return {_trim(str(key)): _trim(value) for key, value in args.items()}
    return _trim(args)


class Auditor:
    """Escribe los eventos ``audit`` en el log JSON.

    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    """

    def __init__(self, logger):
        self._logger = logger

    def emit(self, action, outcome, request_id, client, **fields):
        """Escribe un evento ``audit``.

        :param action: Acción (``tool_call``, ``fallback``, ...)
        :type action: str
        :param outcome: Resultado de la acción (``allowed``, ``denied``, ...)
        :type outcome: str
        :param request_id: Id de la petición
        :type request_id: str
        :param client: Nombre del cliente (nunca su clave), o ``None``
        :type client: str or None
        :param fields: Campos propios de la acción, sin texto ni claves
        :type fields: dict
        :raises AuditFieldError: Si algún campo puede llevar texto o claves
        """
        forbidden = FORBIDDEN_FIELDS.intersection(fields)
        if forbidden:
            raise AuditFieldError(f"audit fields not allowed: {sorted(forbidden)}")
        self._logger.emit(
            AUDIT_EVENT,
            request_id,
            client=client,
            action=action,
            outcome=outcome,
            **fields,
        )

    def bind(self, request_id, client):
        """Crea un auditor fijo para una petición.

        :param request_id: Id de la petición
        :type request_id: str
        :param client: Nombre del cliente, o ``None``
        :type client: str or None
        :return: Función ``(action, outcome, **campos)`` que audita
        :rtype: collections.abc.Callable
        """

        def emit(action, outcome, **fields):
            """Audita una acción de la petición."""
            self.emit(action, outcome, request_id, client, **fields)

        return emit
