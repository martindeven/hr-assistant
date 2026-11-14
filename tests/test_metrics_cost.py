"""Pruebas del costo acumulado en las métricas del proceso."""

import pytest

from app.metrics import MetricsRecorder


def test_metrics_cost_starts_at_zero_and_adds_up():
    metrics = MetricsRecorder(slo_p95_ms=2000)
    assert metrics.snapshot(pid=1)["cost_usd"] == 0.0

    metrics.add_cost(0.25)
    metrics.add_cost(0.000001)

    assert metrics.snapshot(pid=1)["cost_usd"] == pytest.approx(0.250001)
