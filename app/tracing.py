"""Trazas opcionales en LangSmith.

Apagado, el ``Tracer`` no decora ninguna función ni crea un ``Client``, así que
el producto no intenta conectarse aunque ``langsmith`` lea su propia variable
``LANGSMITH_TRACING``. La clave ``LANGSMITH_API_KEY`` la lee ``langsmith`` del
entorno; este módulo nunca la toca.
"""

import contextlib

from langsmith import Client, traceable, tracing_context

# Atributos que ``langsmith`` lee del cliente aunque no se use OpenTelemetry. Un
# cliente inyectado (por ejemplo, uno de pruebas) puede no tenerlos.
OPTIONAL_CLIENT_ATTRIBUTES = {"otel_exporter": None}


class _ClientAdapter:
    """Delega en un cliente inyectado y completa los atributos opcionales.

    :param client: Cliente que manda o guarda las corridas
    :type client: object
    """

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        """Busca el atributo en el cliente; si falta y es opcional, da su valor.

        :param name: Nombre del atributo
        :type name: str
        :return: El valor del atributo
        :rtype: object
        :raises AttributeError: Si falta y no es opcional
        """
        try:
            return getattr(self._client, name)
        except AttributeError:
            if name in OPTIONAL_CLIENT_ATTRIBUTES:
                return OPTIONAL_CLIENT_ATTRIBUTES[name]
            raise


def _adapt(client):
    """Adapta un cliente inyectado solo si le faltan atributos opcionales.

    :param client: Cliente inyectado
    :type client: object
    :return: El mismo cliente, o un adaptador que lo envuelve
    :rtype: object
    """
    if all(hasattr(client, name) for name in OPTIONAL_CLIENT_ATTRIBUTES):
        return client
    return _ClientAdapter(client)


class Tracer:
    """Envuelve funciones como trazas de LangSmith cuando está encendido.

    :param enabled: Si se mandan trazas a LangSmith
    :type enabled: bool
    :param client: Cliente de LangSmith; por omisión se crea uno al encender
    :type client: langsmith.Client or None
    :param hide_inputs: Si no se mandan las entradas de las trazas
    :type hide_inputs: bool
    :param hide_outputs: Si no se mandan las salidas de las trazas
    :type hide_outputs: bool
    """

    def __init__(self, enabled, client=None, hide_inputs=True, hide_outputs=True):
        self._enabled = enabled
        self._client = None
        if enabled and client is not None:
            self._client = _adapt(client)
        elif enabled:
            self._client = Client(hide_inputs=hide_inputs, hide_outputs=hide_outputs)

    @property
    def enabled(self):
        """Indica si el ``Tracer`` manda trazas.

        :return: ``True`` si está encendido
        :rtype: bool
        """
        return self._enabled

    def wrap(self, function, name, run_type):
        """Envuelve una función para que cada llamada quede como traza.

        :param function: Función a envolver
        :type function: collections.abc.Callable
        :param name: Nombre de la traza
        :type name: str
        :param run_type: Tipo de traza (``chain``, ``llm`` o ``tool``)
        :type run_type: str
        :return: La función envuelta, o la misma si el ``Tracer`` está apagado
        :rtype: collections.abc.Callable
        """
        if not self._enabled:
            return function
        return traceable(name=name, run_type=run_type, client=self._client)(function)

    def run_context(self, request_id, client=None):
        """Crea el contexto de una petición con su id y su cliente en los metadatos.

        El cliente es el nombre configurado en ``API_KEYS``, nunca la clave.

        :param request_id: Id de la petición
        :type request_id: str
        :param client: Nombre del cliente que preguntó, o ``None``
        :type client: str or None
        :return: Un administrador de contexto
        :rtype: contextlib.AbstractContextManager
        """
        if not self._enabled:
            return contextlib.nullcontext()
        return tracing_context(
            enabled=True,
            client=self._client,
            metadata={"request_id": request_id, "client": client},
        )
