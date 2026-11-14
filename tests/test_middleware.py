"""Pruebas del middleware de observabilidad a través de la API."""

import uuid

import pytest


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("demo-123", True),
        ("A.b_c-9", True),
        ("a" * 128, True),
        ("", False),
        ("a" * 129, False),
        ("demo;drop", False),
        ("demo/123", False),
    ],
    ids=[
        "simple",
        "dots_and_underscores",
        "longest_allowed",
        "empty",
        "too_long",
        "semicolon",
        "slash",
    ],
)
def test_client_request_id_is_kept_only_when_it_is_safe(
    make_observed_client, sent, kept
):
    observed = make_observed_client()

    response = observed.http.get("/health", headers={"X-Request-ID": sent})

    returned = response.headers["X-Request-ID"]
    if kept:
        assert returned == sent
    else:
        assert uuid.UUID(returned)
    # El evento ``request`` del log usa el mismo id que el encabezado.
    assert observed.events()[-1]["request_id"] == returned


def test_metrics_endpoint_does_not_count_itself(make_observed_client):
    observed = make_observed_client()

    observed.http.get("/health")
    observed.http.get("/metrics")
    metrics = observed.http.get("/metrics").json()

    assert metrics["requests"] == 1
    assert metrics["error_rate"] == 0.0
    assert isinstance(metrics["pid"], int)
