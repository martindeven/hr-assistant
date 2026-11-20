"""Pasos medidos de cada agente en una petición (reto, parte 2, puntos 1 y 2).

Un ``StepRecorder`` vive lo que dura una petición: mide la latencia de cada
agente con el reloj inyectado, abre su nodo en la traza y fija su ``outcome``.
Sus ``steps`` alimentan el evento ``agent_step`` y las métricas por agente.
"""

import contextlib
from collections import deque
from dataclasses import dataclass

from app.guard import GuardBlocked
from app.pipeline import Forbidden

OUTCOME_OK = "ok"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FORBIDDEN = "forbidden"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ERROR = "error"
MILLISECONDS_PER_SECOND = 1000
LATENCY_DECIMALS = 2


@dataclass(frozen=True)
class AgentStep:
    """Lo que hizo un agente en una petición.

    :param agent: Nombre del agente
    :type agent: str
    :param outcome: ``ok``, ``blocked``, ``forbidden``, ``skipped`` o ``error``
    :type outcome: str
    :param latency_ms: Lo que tardó, en milisegundos (0 si no corrió)
    :type latency_ms: float
    """

    agent: str
    outcome: str
    latency_ms: float = 0.0


def outcome_for(error):
    """Traduce la excepción de un agente a su ``outcome``.

    :param error: Excepción que salió del agente
    :type error: BaseException
    :return: ``blocked``, ``forbidden`` o ``error``
    :rtype: str
    """
    if isinstance(error, GuardBlocked):
        return OUTCOME_BLOCKED
    if isinstance(error, Forbidden):
        return OUTCOME_FORBIDDEN
    return OUTCOME_ERROR


class StepRecorder:
    """Registra los pasos de los agentes de una petición.

    :param clock: Reloj inyectado (se usa ``monotonic``)
    :type clock: app.clock.Clock
    :param tracer: Trazas de los agentes
    :type tracer: app.tracing.Tracer
    :param environment: Ambiente (``development`` o ``production``)
    :type environment: str
    """

    def __init__(self, clock, tracer, environment):
        self._clock = clock
        self._tracer = tracer
        self.environment = environment
        self._steps = deque()

    @property
    def steps(self):
        """Pasos registrados, en orden.

        :return: Una copia de los pasos
        :rtype: list[AgentStep]
        """
        return list(self._steps)

    @contextlib.contextmanager
    def step(self, agent):
        """Mide un agente: su nodo en la traza, su latencia y su ``outcome``.

        La excepción del agente se registra y se vuelve a lanzar.

        :param agent: Nombre del agente
        :type agent: str
        :return: Un administrador de contexto que entrega el nodo del agente
        :rtype: contextlib.AbstractContextManager
        """
        started = self._clock.monotonic()
        outcome = OUTCOME_OK
        with self._tracer.agent_span(agent) as span:
            try:
                yield span
            except Exception as error:
                outcome = outcome_for(error)
                raise
            finally:
                elapsed = self._clock.monotonic() - started
                latency_ms = round(elapsed * MILLISECONDS_PER_SECOND, LATENCY_DECIMALS)
                span.annotate(agent=agent, outcome=outcome)
                self._steps.append(AgentStep(agent, outcome, latency_ms))

    def skip(self, agent):
        """Registra un agente que no corrió (``skipped``).

        :param agent: Nombre del agente
        :type agent: str
        """
        self._steps.append(AgentStep(agent, OUTCOME_SKIPPED))

    def ran(self):
        """Agentes que corrieron (sin ``skipped``), sin repetir y en orden.

        :return: Los nombres
        :rtype: list[str]
        """
        names = [
            step.agent for step in self._steps if step.outcome != OUTCOME_SKIPPED
        ]
        return list(dict.fromkeys(names))
