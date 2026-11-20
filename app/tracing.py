"""Trazas opcionales en LangSmith.

Apagado, el ``Tracer`` no decora ninguna función ni crea un ``Client``, así que
el producto no intenta conectarse aunque ``langsmith`` lea su propia variable
``LANGSMITH_TRACING``. La clave ``LANGSMITH_API_KEY`` la lee ``langsmith`` del
entorno; este módulo nunca la toca.
"""

import contextlib

from langsmith import Client, trace, traceable, tracing_context

# Atributos que ``langsmith`` lee del cliente aunque no se use OpenTelemetry. Un
# cliente inyectado (por ejemplo, uno de pruebas) puede no tenerlos.
OPTIONAL_CLIENT_ATTRIBUTES = {"otel_exporter": None}
DEFAULT_PROJECT = "hr-assistant"
ROOT_NAME = "ask"
CHAIN_RUN_TYPE = "chain"
AGENT_METADATA = "agent"
AGENTS_METADATA = "agents"
ID_FIELDS = frozenset({"id", "run_id", "parent_run_id", "trace_id"})


def _text_ids(run):
    """Copia los datos de una corrida con sus ids como texto.

    :param run: Datos de la corrida
    :type run: dict
    :return: Los mismos datos, con ``id``, ``run_id``, ``parent_run_id`` y
        ``trace_id`` como texto
    :rtype: dict
    """
    return {
        key: str(value) if key in ID_FIELDS and value is not None else value
        for key, value in run.items()
    }


class _ClientAdapter:
    """Delega en un cliente inyectado y completa los atributos opcionales.

    Entrega los ids de cada corrida como texto, igual que los guarda LangSmith,
    para que un cliente de pruebas pueda unir padres e hijos.

    :param client: Cliente que manda o guarda las corridas
    :type client: object
    """

    def __init__(self, client):
        self._client = client

    def create_run(self, **run):
        """Manda una corrida nueva al cliente inyectado.

        :param run: Datos de la corrida
        :type run: dict
        :return: Lo que devuelva el cliente
        :rtype: object
        """
        return self._client.create_run(**_text_ids(run))

    def update_run(self, *args, **run):
        """Manda la actualización de una corrida al cliente inyectado.

        :param args: Argumentos posicionales (el id de la corrida)
        :type args: tuple
        :param run: Datos de la corrida
        :type run: dict
        :return: Lo que devuelva el cliente
        :rtype: object
        """
        return self._client.update_run(
            *(str(arg) for arg in args), **_text_ids(run)
        )

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
    """Adapta un cliente inyectado (por ejemplo, uno de pruebas).

    Un ``langsmith.Client`` real se usa tal cual.

    :param client: Cliente inyectado
    :type client: object
    :return: El mismo cliente, o un adaptador que lo envuelve
    :rtype: object
    """
    if isinstance(client, Client):
        return client
    return _ClientAdapter(client)


class _NullSpan:
    """Nodo que no hace nada: lo usan las trazas apagadas."""

    def annotate(self, **metadata):
        """Ignora los metadatos.

        :param metadata: Metadatos del nodo
        :type metadata: dict
        """
        del metadata


NULL_SPAN = _NullSpan()


class _Span:
    """Nodo vivo de una traza: suma metadatos y etiquetas a su corrida.

    :param run_tree: Corrida de LangSmith del nodo
    :type run_tree: langsmith.run_trees.RunTree
    """

    def __init__(self, run_tree):
        self._run_tree = run_tree

    def annotate(self, **metadata):
        """Agrega metadatos al nodo; ``agents`` también se agrega como etiquetas.

        Solo deben llegar ids, conteos y nombres: nunca el texto de la pregunta
        ni una clave.

        :param metadata: Metadatos del nodo
        :type metadata: dict
        """
        self._run_tree.add_metadata(dict(metadata))
        agents = metadata.get(AGENTS_METADATA)
        if agents:
            known = set(self._run_tree.tags or [])
            self._run_tree.add_tags(
                [str(agent) for agent in dict.fromkeys(agents) if agent not in known]
            )


@contextlib.contextmanager
def _null_span():
    """Contexto de un nodo apagado.

    :return: Un administrador de contexto que entrega ``NULL_SPAN``
    :rtype: contextlib.AbstractContextManager
    """
    yield NULL_SPAN


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
    :param project_name: Proyecto de LangSmith (``LANGSMITH_PROJECT``)
    :type project_name: str
    """

    # La firma suma ``project_name`` a la de la sesión 2 sin romperla.
    def __init__(  # pylint: disable=too-many-arguments
        self,
        enabled,
        client=None,
        hide_inputs=True,
        hide_outputs=True,
        *,
        project_name=DEFAULT_PROJECT,
    ):
        self._enabled = enabled
        self._client = None
        self._project_name = project_name
        if enabled and client is not None:
            self._client = _adapt(client)
        elif enabled:
            self._client = Client(hide_inputs=hide_inputs, hide_outputs=hide_outputs)

    @property
    def project_name(self):
        """Proyecto de LangSmith al que van las trazas.

        :return: El nombre del proyecto
        :rtype: str
        """
        return self._project_name

    @contextlib.contextmanager
    def request_span(self, request_id, client, environment):
        """Abre el nodo raíz ``ask`` (tipo ``chain``) de una petición.

        Lleva ``request_id``, ``client`` y ``environment`` en los metadatos y el
        ambiente como etiqueta; el resto se suma con ``annotate``. No manda
        entradas ni salidas. Apagado, entrega un nodo que no hace nada.

        :param request_id: Id de la petición
        :type request_id: str
        :param client: Nombre del cliente (nunca su clave), o ``None``
        :type client: str or None
        :param environment: ``development`` o ``production``
        :type environment: str
        :return: Un administrador de contexto que entrega el nodo
        :rtype: contextlib.AbstractContextManager
        """
        if not self._enabled:
            with _null_span() as span:
                yield span
            return
        metadata = {
            "request_id": request_id,
            "client": client,
            "environment": environment,
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                tracing_context(
                    enabled=True,
                    client=self._client,
                    project_name=self._project_name,
                )
            )
            run_tree = stack.enter_context(
                trace(
                    ROOT_NAME,
                    run_type=CHAIN_RUN_TYPE,
                    inputs={},
                    project_name=self._project_name,
                    tags=[environment],
                    metadata=metadata,
                    client=self._client,
                )
            )
            yield _Span(run_tree)

    @contextlib.contextmanager
    def agent_span(self, agent):
        """Abre el nodo de un agente (tipo ``chain``), hijo del nodo actual.

        Lleva ``agent`` en los metadatos y su nombre como etiqueta; el
        ``outcome`` se suma con ``annotate``. Apagado, no hace nada.

        :param agent: Nombre del agente (``guard``, ``supervisor``, ...)
        :type agent: str
        :return: Un administrador de contexto que entrega el nodo
        :rtype: contextlib.AbstractContextManager
        """
        if not self._enabled:
            with _null_span() as span:
                yield span
            return
        with trace(
            agent,
            run_type=CHAIN_RUN_TYPE,
            inputs={},
            tags=[agent],
            metadata={AGENT_METADATA: agent},
            client=self._client,
        ) as run_tree:
            yield _Span(run_tree)

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
