"""Métricas en memoria del proceso: técnicas, de negocio y alertas.

Guarda conteos acumulados (para Prometheus) y ventanas fijas de las últimas
peticiones (latencias globales, por agente y bloqueos), todo bajo el mismo
candado. Las etiquetas son acotadas: agente, outcome, modelo, motivo y tipo;
``by_client`` solo sale en ``/metrics``, nunca en Prometheus.
"""

import threading
from collections import Counter, defaultdict, deque

from app.alerts import AlertThresholds, evaluate

DEFAULT_WINDOW = 1000
SERVER_ERROR_STATUS = 500
LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000)
SKIPPED_OUTCOME = "skipped"
ERROR_OUTCOME = "error"
CACHE_NONE = "none"
CACHE_RESPONSE = "response"
CACHE_GATEWAY = "gateway"
GUARDRAIL_TYPES = (
    "pii_input",
    "pii_output",
    "injection_blocked",
    "forbidden",
    "output_replaced",
)
# Tipos que siempre son un bloqueo (``pii_input`` lo es solo en modo ``block``).
BLOCKING_TYPES = frozenset({"injection_blocked", "forbidden"})
PII_WHERE_TO_TYPE = {"input": "pii_input", "output": "pii_output"}
STATE_ON = "on"
STATE_OFF = "off"


def _nearest_rank(sorted_values, percentile):
    """Calcula un percentil por el método del rango más cercano.

    :param sorted_values: Valores ordenados de menor a mayor
    :type sorted_values: list[float]
    :param percentile: Percentil entre 1 y 100
    :type percentile: int
    :return: El valor del percentil, o 0 si no hay valores
    :rtype: float
    """
    if not sorted_values:
        return 0
    # Aritmética entera para que el redondeo no dependa de los flotantes.
    rank = max(1, -(-percentile * len(sorted_values) // 100))
    return sorted_values[rank - 1]


def guardrail_from_audit(action, outcome, fields):
    """Traduce un evento ``audit`` a un evento de ``guardrails``.

    :param action: Acción auditada
    :type action: str
    :param outcome: Resultado de la acción
    :type outcome: str
    :param fields: Campos del evento
    :type fields: dict
    :return: El tipo y si es un bloqueo, o ``None`` si no cuenta
    :rtype: tuple[str, bool] or None
    """
    if action == "pii_detected":
        kind = PII_WHERE_TO_TYPE.get(fields.get("where"))
        return (kind, outcome == "blocked") if kind else None
    if action in BLOCKING_TYPES:
        return action, True
    if action == "output_reviewed" and outcome == "replaced":
        return "output_replaced", False
    return None


# Registros planos de conteos: cada uno es un campo que se lee por nombre, así
# que pasan del límite de atributos de pylint sin agregar complejidad.
class _Usage:  # pylint: disable=too-many-instance-attributes
    """Conteos acumulados de negocio (respuestas, costo, caché y modelos)."""

    def __init__(self):
        self.tokens = {"input": 0, "output": 0}
        self.cost_usd = 0.0
        self.answers = 0
        self.answers_cost_usd = 0.0
        self.fallbacks = 0
        self.cache_hits = Counter()
        self.by_client = defaultdict(lambda: {"requests": 0, "cost_usd": 0.0})
        self.by_model = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0})
        self.by_route = Counter()


class _Technical:  # pylint: disable=too-many-instance-attributes
    """Conteos acumulados y ventanas técnicas (peticiones, agentes, bloqueos).

    :param window: Cuántas muestras recientes se guardan
    :type window: int
    """

    def __init__(self, window):
        self.window = window
        self.latencies = deque(maxlen=window)
        self.requests = 0
        self.server_errors = 0
        self.statuses = Counter()
        self.buckets = [0] * len(LATENCY_BUCKETS_MS)
        self.latency_sum = 0.0
        self.agent_runs = Counter()
        self.agent_latencies = defaultdict(lambda: deque(maxlen=window))
        self.guardrails = Counter()
        self.blocks = deque(maxlen=window)
        self.pending_blocks = 0
        self.active_alerts = set()


class MetricsRecorder:
    """Guarda las métricas del proceso con ventanas fijas.

    :param slo_p95_ms: Meta de latencia p95 en milisegundos
    :type slo_p95_ms: int
    :param window: Cuántas muestras recientes se guardan
    :type window: int
    :param thresholds: Umbrales de las alertas; por omisión los de la spec con
        ``slo_p95_ms``
    :type thresholds: app.alerts.AlertThresholds or None
    """

    def __init__(self, slo_p95_ms, window=DEFAULT_WINDOW, thresholds=None):
        self._slo_p95_ms = slo_p95_ms
        self._thresholds = thresholds or AlertThresholds(slo_p95_ms=slo_p95_ms)
        self._tech = _Technical(window)
        self._usage = _Usage()
        self._lock = threading.Lock()

    def record_request(self, latency_ms, status):
        """Registra una petición terminada.

        :param latency_ms: Latencia de la petición en milisegundos
        :type latency_ms: float
        :param status: Código HTTP de la respuesta
        :type status: int
        """
        with self._lock:
            tech = self._tech
            tech.requests += 1
            if status >= SERVER_ERROR_STATUS:
                tech.server_errors += 1
            tech.latencies.append(latency_ms)
            tech.statuses[str(status)] += 1
            tech.latency_sum += latency_ms
            for index, bound in enumerate(LATENCY_BUCKETS_MS):
                if latency_ms <= bound:
                    tech.buckets[index] += 1
            tech.blocks.append(tech.pending_blocks)
            tech.pending_blocks = 0

    def add_tokens(self, input_tokens, output_tokens):
        """Suma los tokens de una corrida del agente.

        :param input_tokens: Tokens de entrada
        :type input_tokens: int
        :param output_tokens: Tokens de salida
        :type output_tokens: int
        """
        with self._lock:
            self._usage.tokens["input"] += input_tokens
            self._usage.tokens["output"] += output_tokens

    def add_cost(self, cost_usd):
        """Suma el costo de una respuesta.

        :param cost_usd: Costo de la respuesta en USD
        :type cost_usd: float
        """
        with self._lock:
            self._usage.cost_usd += cost_usd

    def record_agent(self, agent, outcome, latency_ms):
        """Registra el paso de un agente; ``skipped`` no cuenta en ``runs``.

        :param agent: Nombre del agente
        :type agent: str
        :param outcome: ``ok``, ``blocked``, ``forbidden``, ``skipped`` o
            ``error``
        :type outcome: str
        :param latency_ms: Latencia del paso en milisegundos
        :type latency_ms: float
        """
        with self._lock:
            self._tech.agent_runs[(agent, outcome)] += 1
            if outcome != SKIPPED_OUTCOME:
                self._tech.agent_latencies[agent].append(latency_ms)

    # Cada argumento es un campo de la respuesta que se cuenta por separado.
    def record_answer(  # pylint: disable=too-many-arguments
        self, *, client, model, route, cost_usd, calls, cache, fallback
    ):
        """Registra una respuesta ``200`` con sus datos de negocio.

        :param client: Nombre del cliente, o ``None``
        :type client: str or None
        :param model: Modelo que respondió
        :type model: str
        :param route: Motivo del enrutador
        :type route: str
        :param cost_usd: Costo de la respuesta en USD
        :type cost_usd: float
        :param calls: Llamadas al modelo (``steps``)
        :type calls: int
        :param cache: ``none``, ``gateway`` o ``response``
        :type cache: str
        :param fallback: Si respondió el modelo de respaldo
        :type fallback: bool
        """
        with self._lock:
            usage = self._usage
            usage.answers += 1
            usage.answers_cost_usd += cost_usd
            if client is not None:
                usage.by_client[client]["cost_usd"] += cost_usd
            usage.by_model[model]["calls"] += calls
            usage.by_model[model]["cost_usd"] += cost_usd
            usage.by_route[route] += 1
            if cache != CACHE_NONE:
                usage.cache_hits[cache] += 1
            if fallback:
                usage.fallbacks += 1

    def record_client_request(self, client):
        """Cuenta una petición a ``/ask`` de un cliente (con cualquier código).

        :param client: Nombre del cliente
        :type client: str
        """
        with self._lock:
            self._usage.by_client[client]["requests"] += 1

    def record_guardrail(self, kind, blocked=False):
        """Cuenta un evento de ``guardrails`` y, si es bloqueo, para la alerta.

        :param kind: Tipo (``pii_input``, ``injection_blocked``, ...)
        :type kind: str
        :param blocked: Si la petición se bloqueó por este evento
        :type blocked: bool
        """
        with self._lock:
            self._tech.guardrails[kind] += 1
            if blocked or kind in BLOCKING_TYPES:
                self._tech.pending_blocks += 1

    def observe_audit(self, action, outcome, fields):
        """Observador del ``Auditor``: cuenta ``guardrails`` desde el audit.

        :param action: Acción auditada
        :type action: str
        :param outcome: Resultado de la acción
        :type outcome: str
        :param fields: Campos del evento
        :type fields: dict
        """
        found = guardrail_from_audit(action, outcome, fields)
        if found is not None:
            self.record_guardrail(*found)

    def _alert_values(self):
        """Valores que miran las alertas (se llama con el candado tomado).

        :return: ``p95_ms``, ``error_rate``, ``cost_usd_per_request`` y
            ``guardrail_events``
        :rtype: dict
        """
        tech, usage = self._tech, self._usage
        return {
            "p95_ms": _nearest_rank(sorted(tech.latencies), 95),
            "error_rate": (
                tech.server_errors / tech.requests if tech.requests else 0.0
            ),
            "cost_usd_per_request": (
                usage.answers_cost_usd / usage.answers if usage.answers else 0.0
            ),
            "guardrail_events": sum(tech.blocks) + tech.pending_blocks,
        }

    def check_alerts(self):
        """Compara las alertas con las de la última revisión.

        :return: Las alertas que cambiaron, con su estado nuevo (``on`` u
            ``off``), en orden
        :rtype: list[tuple[str, str]]
        """
        with self._lock:
            active = set(evaluate(self._alert_values(), self._thresholds))
            previous = self._tech.active_alerts
            self._tech.active_alerts = active
        changes = [(name, STATE_ON) for name in sorted(active - previous)]
        changes.extend((name, STATE_OFF) for name in sorted(previous - active))
        return changes

    def _by_agent(self):
        """Corridas, errores y p95 por agente (con el candado tomado).

        :return: Un diccionario por agente
        :rtype: dict
        """
        tech = self._tech
        agents = {agent for agent, _ in tech.agent_runs}
        result = {}
        for agent in sorted(agents):
            runs = sum(
                count
                for (name, outcome), count in tech.agent_runs.items()
                if name == agent and outcome != SKIPPED_OUTCOME
            )
            result[agent] = {
                "runs": runs,
                "errors": tech.agent_runs[(agent, ERROR_OUTCOME)],
                "p95_ms": _nearest_rank(sorted(tech.agent_latencies[agent]), 95),
            }
        return result

    def _business(self):
        """Campos de negocio de ``/metrics`` (con el candado tomado).

        :return: ``by_client``, ``by_model``, ``by_route``, ``cache``,
            ``fallbacks`` y ``cost_usd_per_request``
        :rtype: dict
        """
        usage = self._usage
        hits = usage.cache_hits
        return {
            "by_client": {name: dict(data) for name, data in usage.by_client.items()},
            "by_model": {name: dict(data) for name, data in usage.by_model.items()},
            "by_route": dict(usage.by_route),
            "cache": {
                "response_hits": hits[CACHE_RESPONSE],
                "gateway_hits": hits[CACHE_GATEWAY],
                "hit_rate": (
                    sum(hits.values()) / usage.answers if usage.answers else 0
                ),
            },
            "fallbacks": usage.fallbacks,
            "cost_usd_per_request": (
                usage.answers_cost_usd / usage.answers if usage.answers else 0
            ),
        }

    def snapshot(self, pid):
        """Devuelve el estado actual de las métricas, listo para ``/metrics``.

        :param pid: Id del proceso que responde
        :type pid: int
        :return: Los campos de las sesiones 1 a 4 (peticiones, tasa de error,
            percentiles, tokens, costo y SLO) y los del reto: por agente,
            cliente, modelo y motivo, ``guardrails``, caché, respaldos, costo
            por respuesta y alertas encendidas
        :rtype: dict
        """
        with self._lock:
            tech = self._tech
            latencies = sorted(tech.latencies)
            values = self._alert_values()
            body = {
                "pid": pid,
                "requests": tech.requests,
                "error_rate": values["error_rate"],
                "latency_ms": {
                    "p50": _nearest_rank(latencies, 50),
                    "p95": values["p95_ms"],
                },
                "tokens": dict(self._usage.tokens),
                "cost_usd": self._usage.cost_usd,
                "slo": {
                    "p95_ms_target": self._slo_p95_ms,
                    "met": values["p95_ms"] < self._slo_p95_ms,
                },
                "by_agent": self._by_agent(),
                "guardrails": {
                    kind: tech.guardrails[kind] for kind in GUARDRAIL_TYPES
                },
                **self._business(),
            }
        body["alerts"] = evaluate(values, self._thresholds)
        return body

    def prometheus_counters(self):
        """Conteos acumulados del proceso para ``/metrics/prometheus``.

        :return: Estados, histograma de latencia, corridas por agente, tokens,
            costo por modelo, ``guardrails`` y aciertos de caché
        :rtype: dict
        """
        with self._lock:
            tech, usage = self._tech, self._usage
            return {
                "requests": dict(tech.statuses),
                "latency": {
                    "buckets": list(zip(LATENCY_BUCKETS_MS, tech.buckets)),
                    "count": tech.requests,
                    "sum": tech.latency_sum,
                },
                "agent_runs": dict(tech.agent_runs),
                "tokens": dict(usage.tokens),
                "cost_by_model": {
                    name: data["cost_usd"] for name, data in usage.by_model.items()
                },
                "guardrails": {
                    kind: tech.guardrails[kind] for kind in GUARDRAIL_TYPES
                },
                "cache_hits": {
                    CACHE_RESPONSE: usage.cache_hits[CACHE_RESPONSE],
                    CACHE_GATEWAY: usage.cache_hits[CACHE_GATEWAY],
                },
            }
