"""Alertas del proceso (reto, parte 2, punto 4): una función pura con 4 reglas.

Cada regla se enciende solo si el valor queda **estrictamente** por encima de su
umbral; justo en el umbral sigue apagada.
"""

from dataclasses import dataclass

P95_ABOVE_SLO = "p95_above_slo"
ERROR_RATE_HIGH = "error_rate_high"
COST_PER_REQUEST_HIGH = "cost_per_request_high"
GUARDRAIL_SPIKE = "guardrail_spike"
DEFAULT_ERROR_RATE = 0.05
DEFAULT_COST_PER_REQUEST_USD = 0.001
DEFAULT_GUARDRAIL_EVENTS = 10


@dataclass(frozen=True)
class AlertThresholds:
    """Umbrales de las alertas.

    :param slo_p95_ms: Meta de latencia p95 en milisegundos (``SLO_P95_MS``)
    :type slo_p95_ms: float
    :param error_rate: Tasa de error máxima (``ALERT_ERROR_RATE``)
    :type error_rate: float
    :param cost_per_request_usd: Costo promedio máximo por respuesta
        (``ALERT_COST_PER_REQUEST_USD``)
    :type cost_per_request_usd: float
    :param guardrail_events: Bloqueos máximos en la ventana
        (``ALERT_GUARDRAIL_EVENTS``)
    :type guardrail_events: int
    """

    slo_p95_ms: float
    error_rate: float = DEFAULT_ERROR_RATE
    cost_per_request_usd: float = DEFAULT_COST_PER_REQUEST_USD
    guardrail_events: int = DEFAULT_GUARDRAIL_EVENTS


def evaluate(values, thresholds):
    """Calcula las alertas encendidas.

    :param values: Valores actuales con las llaves ``p95_ms``, ``error_rate``,
        ``cost_usd_per_request`` y ``guardrail_events``
    :type values: dict
    :param thresholds: Umbrales de las alertas
    :type thresholds: AlertThresholds
    :return: Los nombres de las alertas encendidas, en orden fijo
    :rtype: list[str]
    """
    rules = (
        (P95_ABOVE_SLO, values["p95_ms"], thresholds.slo_p95_ms),
        (ERROR_RATE_HIGH, values["error_rate"], thresholds.error_rate),
        (
            COST_PER_REQUEST_HIGH,
            values["cost_usd_per_request"],
            thresholds.cost_per_request_usd,
        ),
        (GUARDRAIL_SPIKE, values["guardrail_events"], thresholds.guardrail_events),
    )
    return [name for name, value, limit in rules if value > limit]
