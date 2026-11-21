"""Métricas por agente, Prometheus y alertas (reto, parte 2, puntos 3 y 4)."""

import math
import os

import pytest

from app.alerts import AlertThresholds, evaluate
from app.metrics import MetricsRecorder
from app.prometheus import escape_label_value

LATENCY_HISTOGRAM = "hr_request_latency_ms"


def test_agent_p95_counts_runs_and_errors_but_not_skipped_steps():
    metrics = MetricsRecorder(slo_p95_ms=2000)

    for latency_ms in range(1, 101):
        metrics.record_agent("directory", "ok", latency_ms)
    metrics.record_agent("guard", "ok", 3)
    metrics.record_agent("guard", "error", 4)
    metrics.record_agent("guard", "skipped", 0)

    by_agent = metrics.snapshot(pid=1)["by_agent"]
    assert by_agent["directory"] == {"runs": 100, "errors": 0, "p95_ms": 95}
    assert by_agent["guard"]["runs"] == 2
    assert by_agent["guard"]["errors"] == 1


def test_cost_per_request_and_hit_rate_only_use_answered_requests():
    metrics = MetricsRecorder(slo_p95_ms=2000)
    empty = metrics.snapshot(pid=1)

    metrics.record_answer(
        client="portal", model="small", route="simple", cost_usd=0.004,
        calls=2, cache="none", fallback=False,
    )
    metrics.record_answer(
        client="portal", model="small", route="simple", cost_usd=0.0,
        calls=0, cache="response", fallback=False,
    )
    metrics.record_request(latency_ms=10, status=502)
    full = metrics.snapshot(pid=1)

    assert empty["cost_usd_per_request"] == 0
    assert empty["cache"]["hit_rate"] == 0
    assert empty["alerts"] == [] and empty["fallbacks"] == 0
    assert full["cost_usd_per_request"] == pytest.approx(0.002)
    assert full["cache"]["response_hits"] == 1
    assert full["cache"]["hit_rate"] == pytest.approx(0.5)
    assert full["by_model"]["small"]["calls"] == 2
    assert full["by_client"]["portal"]["cost_usd"] == pytest.approx(0.004)
    assert full["by_route"] == {"simple": 2}


def test_metrics_endpoints_are_not_counted_as_requests(make_observed_client):
    http = make_observed_client().http

    http.get("/health")
    http.get("/metrics/prometheus")
    http.get("/metrics")

    assert http.get("/metrics").json()["requests"] == 1


def test_the_latency_histogram_is_cumulative_and_every_series_has_the_pid(
    make_observed_client, prometheus_samples
):
    http = make_observed_client().http
    for _ in range(3):
        http.get("/health")

    samples = prometheus_samples(http.get("/metrics/prometheus").text)

    assert all(labels["pid"] == str(os.getpid()) for _, labels, _ in samples)
    buckets = sorted(
        (float(labels["le"]), value)
        for name, labels, value in samples
        if name == f"{LATENCY_HISTOGRAM}_bucket"
    )
    counts = [value for _, value in buckets]
    assert counts == sorted(counts)
    assert buckets[-1][0] == math.inf
    total = next(v for n, _, v in samples if n == f"{LATENCY_HISTOGRAM}_count")
    assert buckets[-1][1] == total == 3


def test_prometheus_label_values_escape_quotes_backslashes_and_newlines():
    assert escape_label_value('model "x"') == 'model \\"x\\"'
    assert escape_label_value("a\\b") == "a\\\\b"
    assert escape_label_value("a\nb") == "a\\nb"
    assert escape_label_value("@cf/meta/llama") == "@cf/meta/llama"


def test_each_alert_turns_on_only_strictly_above_its_threshold():
    thresholds = AlertThresholds(
        slo_p95_ms=2000,
        error_rate=0.05,
        cost_per_request_usd=0.001,
        guardrail_events=10,
    )
    at_threshold = {
        "p95_ms": 2000,
        "error_rate": 0.05,
        "cost_usd_per_request": 0.001,
        "guardrail_events": 10,
    }
    above = {
        "p95_ms": ("p95_above_slo", 2001),
        "error_rate": ("error_rate_high", 0.06),
        "cost_usd_per_request": ("cost_per_request_high", 0.002),
        "guardrail_events": ("guardrail_spike", 11),
    }

    assert evaluate(at_threshold, thresholds) == []
    for field, (alert, value) in above.items():
        assert evaluate({**at_threshold, field: value}, thresholds) == [alert]
