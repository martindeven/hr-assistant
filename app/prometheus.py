"""Métricas en el formato de texto 0.0.4 de Prometheus, sin librerías.

Cada muestra lleva la etiqueta ``pid`` para sumar entre procesos. Las demás
etiquetas son acotadas (estado, agente, outcome, dirección, modelo, tipo y
capa): nunca ``client``, ``request_id`` ni texto de una pregunta.
"""

import math

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
INFINITY_BOUND = "+Inf"


def escape_label_value(value):
    """Escapa un valor de etiqueta: ``\\``, comillas dobles y saltos de línea.

    :param value: Valor de la etiqueta
    :type value: object
    :return: El valor listo para ir entre comillas
    :rtype: str
    """
    return (
        str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


def _format_value(value):
    """Escribe un número como lo espera Prometheus.

    :param value: Número de la muestra
    :type value: int or float
    :return: El número como texto
    :rtype: str
    """
    if isinstance(value, float) and math.isinf(value):
        return INFINITY_BOUND if value > 0 else "-Inf"
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _sample(name, labels, value):
    """Escribe una muestra con sus etiquetas.

    :param name: Nombre de la serie
    :type name: str
    :param labels: Etiquetas en orden
    :type labels: list[tuple[str, object]]
    :param value: Valor de la muestra
    :type value: int or float
    :return: La línea de la muestra
    :rtype: str
    """
    text = ",".join(
        f'{key}="{escape_label_value(label)}"' for key, label in labels
    )
    return f"{name}{{{text}}} {_format_value(value)}"


def _header(name, kind, help_text):
    """Escribe las líneas ``HELP`` y ``TYPE`` de una serie.

    :param name: Nombre de la serie
    :type name: str
    :param kind: ``counter`` o ``histogram``
    :type kind: str
    :param help_text: Descripción corta
    :type help_text: str
    :return: Las dos líneas
    :rtype: list[str]
    """
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]


def _counter(name, help_text, label, values, pid):
    """Escribe un contador con una etiqueta propia.

    :param name: Nombre de la serie
    :type name: str
    :param help_text: Descripción corta
    :type help_text: str
    :param label: Nombre de la etiqueta propia
    :type label: str
    :param values: Valor por etiqueta
    :type values: dict
    :param pid: Id del proceso
    :type pid: int
    :return: Las líneas de la serie
    :rtype: list[str]
    """
    lines = _header(name, "counter", help_text)
    for key in sorted(values):
        lines.append(_sample(name, [(label, key), ("pid", pid)], values[key]))
    return lines


def _histogram(latency, pid):
    """Escribe el histograma acumulado de latencia (``+Inf`` es ``_count``).

    :param latency: Cubetas ``(límite, conteo)``, ``count`` y ``sum``
    :type latency: dict
    :param pid: Id del proceso
    :type pid: int
    :return: Las líneas de la serie
    :rtype: list[str]
    """
    name = "hr_request_latency_ms"
    lines = _header(name, "histogram", "Request latency in milliseconds")
    for bound, count in latency["buckets"]:
        lines.append(_sample(f"{name}_bucket", [("le", bound), ("pid", pid)], count))
    lines.append(
        _sample(
            f"{name}_bucket", [("le", INFINITY_BOUND), ("pid", pid)], latency["count"]
        )
    )
    lines.append(_sample(f"{name}_sum", [("pid", pid)], latency["sum"]))
    lines.append(_sample(f"{name}_count", [("pid", pid)], latency["count"]))
    return lines


def _agent_runs(agent_runs, pid):
    """Escribe las corridas por agente y ``outcome``.

    :param agent_runs: Conteo por ``(agente, outcome)``
    :type agent_runs: dict
    :param pid: Id del proceso
    :type pid: int
    :return: Las líneas de la serie
    :rtype: list[str]
    """
    name = "hr_agent_runs_total"
    lines = _header(name, "counter", "Agent steps by agent and outcome")
    for (agent, outcome), count in sorted(agent_runs.items()):
        labels = [("agent", agent), ("outcome", outcome), ("pid", pid)]
        lines.append(_sample(name, labels, count))
    return lines


def render(counters, pid):
    """Arma el texto de ``GET /metrics/prometheus``.

    :param counters: Conteos de ``MetricsRecorder.prometheus_counters``
    :type counters: dict
    :param pid: Id del proceso que responde
    :type pid: int
    :return: El texto en formato 0.0.4, con salto de línea final
    :rtype: str
    """
    lines = _counter(
        "hr_requests_total", "Requests by status", "status", counters["requests"], pid
    )
    lines += _histogram(counters["latency"], pid)
    lines += _agent_runs(counters["agent_runs"], pid)
    lines += _counter(
        "hr_tokens_total", "Model tokens", "direction", counters["tokens"], pid
    )
    lines += _counter(
        "hr_cost_usd_total",
        "Model cost in USD",
        "model",
        counters["cost_by_model"],
        pid,
    )
    lines += _counter(
        "hr_guardrail_events_total",
        "Guardrail events by type",
        "type",
        counters["guardrails"],
        pid,
    )
    lines += _counter(
        "hr_cache_hits_total",
        "Answers served from a cache",
        "layer",
        counters["cache_hits"],
        pid,
    )
    return "\n".join(lines) + "\n"
