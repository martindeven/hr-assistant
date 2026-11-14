"""Métricas en memoria del proceso: peticiones, latencia, errores, tokens y costo."""

import threading
from collections import deque

DEFAULT_WINDOW = 1000
SERVER_ERROR_STATUS = 500


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


class MetricsRecorder:
    """Guarda las métricas del proceso con una ventana fija de latencias.

    :param slo_p95_ms: Meta de latencia p95 en milisegundos
    :type slo_p95_ms: int
    :param window: Cuántas latencias recientes se guardan
    :type window: int
    """

    def __init__(self, slo_p95_ms, window=DEFAULT_WINDOW):
        self._slo_p95_ms = slo_p95_ms
        self._latencies = deque(maxlen=window)
        self._requests = 0
        self._server_errors = 0
        # Consumo acumulado del modelo: tokens de entrada y salida, y costo.
        self._usage = {"input": 0, "output": 0, "cost_usd": 0.0}
        self._lock = threading.Lock()

    def record_request(self, latency_ms, status):
        """Registra una petición terminada.

        :param latency_ms: Latencia de la petición en milisegundos
        :type latency_ms: float
        :param status: Código HTTP de la respuesta
        :type status: int
        """
        with self._lock:
            self._requests += 1
            if status >= SERVER_ERROR_STATUS:
                self._server_errors += 1
            self._latencies.append(latency_ms)

    def add_tokens(self, input_tokens, output_tokens):
        """Suma los tokens de una corrida del agente.

        :param input_tokens: Tokens de entrada
        :type input_tokens: int
        :param output_tokens: Tokens de salida
        :type output_tokens: int
        """
        with self._lock:
            self._usage["input"] += input_tokens
            self._usage["output"] += output_tokens

    def add_cost(self, cost_usd):
        """Suma el costo de una respuesta.

        :param cost_usd: Costo de la respuesta en USD
        :type cost_usd: float
        """
        with self._lock:
            self._usage["cost_usd"] += cost_usd

    def snapshot(self, pid):
        """Devuelve el estado actual de las métricas, listo para ``/metrics``.

        :param pid: Id del proceso que responde
        :type pid: int
        :return: Peticiones, tasa de error, percentiles, tokens, costo
            acumulado en USD (sin redondear) y SLO
        :rtype: dict
        """
        with self._lock:
            latencies = sorted(self._latencies)
            requests = self._requests
            server_errors = self._server_errors
            tokens = {"input": self._usage["input"], "output": self._usage["output"]}
            cost_usd = self._usage["cost_usd"]
        p95 = _nearest_rank(latencies, 95)
        return {
            "pid": pid,
            "requests": requests,
            "error_rate": server_errors / requests if requests else 0.0,
            "latency_ms": {"p50": _nearest_rank(latencies, 50), "p95": p95},
            "tokens": tokens,
            "cost_usd": cost_usd,
            "slo": {"p95_ms_target": self._slo_p95_ms, "met": p95 < self._slo_p95_ms},
        }
