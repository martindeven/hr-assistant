"""Pruebas de ``app.metrics.MetricsRecorder``: latencia, errores, tokens y SLO."""

from app.metrics import MetricsRecorder


def test_metrics_without_requests_are_zero_and_meet_the_slo():
    recorder = MetricsRecorder(slo_p95_ms=2000)

    assert recorder.snapshot(pid=42) == {
        "pid": 42,
        "requests": 0,
        "error_rate": 0.0,
        "latency_ms": {"p50": 0, "p95": 0},
        "tokens": {"input": 0, "output": 0},
        "cost_usd": 0.0,
        "slo": {"p95_ms_target": 2000, "met": True},
    }


def test_percentiles_error_rate_and_tokens_come_from_the_recorded_requests():
    recorder = MetricsRecorder(slo_p95_ms=90)
    for latency in range(10, 101, 10):
        status = 500 if latency in (30, 70) else 200
        recorder.record_request(latency_ms=latency, status=status)
    recorder.add_tokens(input_tokens=10, output_tokens=2)
    recorder.add_tokens(input_tokens=5, output_tokens=1)

    snapshot = recorder.snapshot(pid=42)

    assert snapshot["requests"] == 10
    # Rango más cercano sobre 10 valores: p50 es el 5.o y p95 el 10.o.
    assert snapshot["latency_ms"] == {"p50": 50, "p95": 100}
    assert snapshot["error_rate"] == 0.2
    assert snapshot["tokens"] == {"input": 15, "output": 3}
    # 100 ms no está por debajo de la meta de 90 ms.
    assert snapshot["slo"] == {"p95_ms_target": 90, "met": False}


def test_the_latency_window_forgets_the_oldest_requests():
    recorder = MetricsRecorder(slo_p95_ms=2000, window=3)
    for latency in (5000, 5000, 5000, 10, 10, 10):
        recorder.record_request(latency_ms=latency, status=200)

    snapshot = recorder.snapshot(pid=42)

    assert snapshot["requests"] == 6
    assert snapshot["latency_ms"] == {"p50": 10, "p95": 10}
    assert snapshot["slo"]["met"] is True
