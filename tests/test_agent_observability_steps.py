"""Pasos de ``features/agent_observability.feature`` (reto, parte 2, puntos 2 a 5).

Todo pasa por la API con el modelo simulado, reloj falso y log en memoria.
"""

import os

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/agent_observability.feature")

# Clave del kiosco armada en tiempo de ejecución: no es un secreto (gitleaks).
KIOSK_KEY = "-".join(["kiosk", "test"] * 3)
TEST_CLIENT = "unit-tests"
OLD_METRIC_FIELDS = {
    "pid",
    "requests",
    "error_rate",
    "latency_ms",
    "tokens",
    "cost_usd",
    "slo",
}
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4"


def _split(text):
    """Convierte ``"a, b"`` en ``["a", "b"]``."""
    return [item.strip() for item in text.split(",")]


def _events(observed, name):
    """Los eventos del log con ese nombre."""
    return [event for event in observed.events() if event["event"] == name]


def _kiosk_env(api_key):
    """Entorno con el cliente de pruebas completo y un kiosco solo de políticas."""
    scopes = f"{TEST_CLIENT}:directory policies metrics,kiosk:policies"
    return {
        "API_KEYS": f"{TEST_CLIENT}:{api_key},kiosk:{KIOSK_KEY}",
        "API_KEY_SCOPES": scopes,
    }


@given("the HR assistant is running with the fake model", target_fixture="observed")
def running_with_fake_model(make_observed_client):
    """La API con el modelo simulado y todos los permisos (desarrollo)."""
    return make_observed_client()


@given(
    "the HR assistant is running with a kiosk client limited to policies",
    target_fixture="observed",
)
def running_with_kiosk(make_observed_client, api_key):
    """La API con dos clientes: el de pruebas y un kiosco solo de políticas."""
    return make_observed_client(env=_kiosk_env(api_key))


@given(
    "the HR assistant is running with a guardrail alert threshold of 1",
    target_fixture="observed",
)
def running_with_low_guardrail_threshold(make_observed_client):
    """La alerta de bloqueos se enciende con más de un bloqueo."""
    return make_observed_client(env={"ALERT_GUARDRAIL_EVENTS": "1"})


@given(
    "the HR assistant is running and the client sends no API key",
    target_fixture="observed",
)
def running_without_key(make_observed_client):
    """La API con la clave configurada; el cliente no la manda."""
    return make_observed_client(send_key=False)


@when(parsers.parse('I ask "{question}"'), target_fixture="response")
def ask(observed, question):
    """Manda una pregunta con la clave de pruebas."""
    return observed.http.post("/ask", json={"question": question})


@when(parsers.parse('the kiosk asks "{question}"'), target_fixture="response")
def kiosk_asks(observed, question):
    """Manda una pregunta con la clave del kiosco."""
    return observed.http.post(
        "/ask", json={"question": question}, headers={"X-API-Key": KIOSK_KEY}
    )


@when("I read the metrics", target_fixture="response")
def read_metrics(observed):
    """Consulta ``GET /metrics`` con la clave de pruebas."""
    return observed.http.get("/metrics")


@when("I read the Prometheus metrics", target_fixture="response")
def read_prometheus(observed):
    """Consulta ``GET /metrics/prometheus`` con la clave de pruebas."""
    return observed.http.get("/metrics/prometheus")


@when("I open the dashboard", target_fixture="response")
def open_dashboard(observed):
    """Abre ``GET /dashboard`` sin clave."""
    return observed.http.get("/dashboard")


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then(parsers.parse('the agent steps of "{agents}" are "{outcome}"'))
def agent_steps_are(observed, agents, outcome):
    """Cada agente dejó un ``agent_step`` con ese resultado."""
    steps = {
        step["agent"]: step["outcome"] for step in _events(observed, "agent_step")
    }
    for agent in _split(agents):
        assert steps.get(agent) == outcome, agent


@then("every agent step has the request id, the client and a latency")
def agent_steps_have_fields(observed, response):
    """Los pasos llevan el id de la petición, el cliente y la latencia."""
    steps = _events(observed, "agent_step")
    assert steps
    for step in steps:
        assert step["request_id"] == response.json()["request_id"]
        assert step["client"] == TEST_CLIENT
        assert step["latency_ms"] >= 0


@then("the agent run event reports the agents, the fallback and the cache")
def agent_run_has_new_fields(observed, response):
    """``agent_run`` suma ``agents``, ``fallback`` y ``cache``."""
    run = _events(observed, "agent_run")[-1]
    body = response.json()
    assert run["agents"] == body["agents"]
    assert run["fallback"] is False
    assert run["cache"] == body["cache"] == "none"


@then(parsers.parse('the log does not contain "{text}"'))
def log_does_not_contain(observed, text):
    """El texto de la pregunta nunca llega al log."""
    assert text not in observed.log_stream.getvalue()


@then(parsers.parse('the metrics count {count:d} "{kind}" guardrail event'))
def metrics_count_guardrail(observed, count, kind):
    """``guardrails`` de ``/metrics`` cuenta el bloqueo (con la clave completa)."""
    metrics = observed.http.get("/metrics").json()
    assert metrics["guardrails"][kind] == count


@then("the metrics keep the fields of the earlier sessions")
def metrics_keep_old_fields(response):
    """Los campos de las sesiones 1 a 4 siguen en ``/metrics``."""
    assert OLD_METRIC_FIELDS <= set(response.json())


@then(parsers.parse('the metrics have agent runs for "{agents}"'))
def metrics_have_agent_runs(response, agents):
    """``by_agent`` trae ``runs``, ``errors`` y ``p95_ms`` de cada agente."""
    by_agent = response.json()["by_agent"]
    for agent in _split(agents):
        assert by_agent[agent]["runs"] >= 1, agent
        assert by_agent[agent]["errors"] == 0, agent
        assert by_agent[agent]["p95_ms"] >= 0, agent


@then(parsers.parse("the metrics count {count:d} requests for the test client"))
def metrics_count_client_requests(response, count):
    """``by_client`` cuenta las preguntas y el costo del cliente."""
    client = response.json()["by_client"][TEST_CLIENT]
    assert client["requests"] == count
    assert client["cost_usd"] > 0


@then(
    parsers.parse(
        "the metrics count {calls:d} calls to the small model "
        'and {routes:d} "{route}" routes'
    )
)
def metrics_count_model_and_route(response, settings, calls, routes, route):
    """``by_model`` cuenta las llamadas al modelo y ``by_route`` los motivos."""
    body = response.json()
    assert body["by_model"][settings.model_small]["calls"] == calls
    assert body["by_model"][settings.model_small]["cost_usd"] > 0
    assert body["by_route"] == {route: routes}


@then("the metrics show no cache hits, no fallbacks and no alerts")
def metrics_show_quiet_state(response):
    """Sin caché, sin respaldo y sin alertas encendidas."""
    body = response.json()
    assert body["cache"] == {"response_hits": 0, "gateway_hits": 0, "hit_rate": 0}
    assert body["fallbacks"] == 0
    assert body["alerts"] == []


@then("the cost per request is greater than zero")
def cost_per_request_positive(response):
    """El costo promedio de las respuestas ``200``."""
    assert response.json()["cost_usd_per_request"] > 0


@then("the response is in the Prometheus text format")
def response_is_prometheus_text(response):
    """El tipo de contenido es el del formato de texto 0.0.4."""
    assert response.headers["content-type"].startswith(PROMETHEUS_CONTENT_TYPE)


@then(parsers.parse('the Prometheus text has the series "{names}"'))
def prometheus_has_series(response, names):
    """Están todas las series que pide la especificación."""
    for name in _split(names):
        assert f"# TYPE {name} " in response.text, name


@then("every Prometheus sample carries the pid label")
def prometheus_samples_have_pid(response, prometheus_samples):
    """Cada muestra lleva el ``pid`` del proceso y nunca el cliente."""
    samples = prometheus_samples(response.text)
    assert samples
    for name, labels, _value in samples:
        assert labels.get("pid") == str(os.getpid()), name
        assert "client" not in labels and "request_id" not in labels, name


@then("the Prometheus metrics answer 401 without a key and 403 to the kiosk")
def prometheus_needs_key_and_permission(observed):
    """Sin clave ``401``; con una clave sin el permiso ``metrics``, ``403``."""
    path = "/metrics/prometheus"
    assert observed.http.get(path, headers={"X-API-Key": ""}).status_code == 401
    assert observed.http.get(path, headers={"X-API-Key": KIOSK_KEY}).status_code == 403


@then(parsers.parse('the metrics list the alert "{alert}"'))
def metrics_list_alert(response, alert):
    """La alerta aparece en ``alerts`` de ``/metrics``."""
    assert alert in response.json()["alerts"]


@then(parsers.parse('the log has exactly one "{state}" alert event for "{alert}"'))
def one_alert_event(observed, state, alert):
    """El evento ``alert`` sale solo cuando la alerta cambia de estado."""
    alerts = [
        event for event in _events(observed, "alert") if event["alert"] == alert
    ]
    assert [event["state"] for event in alerts] == [state]


@then(parsers.parse('the page has the test ids "{test_ids}"'))
def page_has_test_ids(response, test_ids):
    """La página trae las marcas que usan las pruebas de punta a punta."""
    for test_id in _split(test_ids):
        assert f'data-testid="{test_id}"' in response.text, test_id


@then("the page has the same Content-Security-Policy as the home page")
def page_has_home_csp(observed, response):
    """``/dashboard`` y ``/`` llevan la misma CSP."""
    home = observed.http.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert csp == home.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
