"""Pasos de los escenarios de ``features/observability.feature``."""

import uuid

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/observability.feature")

UNSAFE_REQUEST_IDS = {
    "with spaces": "demo 123",
    "200 characters long": "a" * 200,
}


def events_for(observed, request_id):
    """Filtra los eventos del log de una petición.

    :param observed: Cliente con su log en memoria
    :type observed: ObservedClient
    :param request_id: Id de la petición
    :type request_id: str
    :return: Los eventos de esa petición, en orden
    :rtype: list[dict]
    """
    return [
        event for event in observed.events() if event["request_id"] == request_id
    ]


@given("the HR assistant is running with an in-memory log", target_fixture="observed")
def observed_client(make_observed_client):
    """La API con reloj falso, log en memoria y el modelo simulado."""
    return make_observed_client()


@given(
    "the HR assistant is running with a model that raises an exception",
    target_fixture="observed",
)
def failing_client(make_observed_client, exploding_model):
    """La API con un modelo que siempre falla."""
    return make_observed_client(model=exploding_model)


@given(
    parsers.parse(
        "the HR assistant is running with a clock that advances "
        "{seconds:d} seconds per reading"
    ),
    target_fixture="observed",
)
def slow_client(make_observed_client, fake_clock, seconds):
    """La API con un reloj que hace que cada petición tarde más que el SLO."""
    return make_observed_client(clock=fake_clock(step_seconds=seconds))


@when("I check the health endpoint without a request id", target_fixture="response")
def health_without_id(observed):
    """Consulta ``GET /health`` sin encabezado."""
    return observed.http.get("/health")


@when(
    parsers.parse("I check the health endpoint with a request id that is {kind}"),
    target_fixture="response",
)
def health_with_unsafe_id(observed, kind):
    """Consulta ``GET /health`` con un ``X-Request-ID`` que no se debe aceptar."""
    return observed.http.get(
        "/health", headers={"X-Request-ID": UNSAFE_REQUEST_IDS[kind]}
    )


@when(
    parsers.parse('I ask "{question}" with the request id "{request_id}"'),
    target_fixture="response",
)
def ask_with_id(observed, question, request_id):
    """Manda una pregunta con un ``X-Request-ID`` válido."""
    return observed.http.post(
        "/ask", json={"question": question}, headers={"X-Request-ID": request_id}
    )


@when("I ask an empty question", target_fixture="response")
def ask_empty(observed):
    """Manda una pregunta vacía."""
    return observed.http.post("/ask", json={"question": ""})


@when("I read the metrics", target_fixture="metrics")
def read_metrics(observed):
    """Consulta ``GET /metrics``."""
    return observed.http.get("/metrics").json()


@when("I open the home page", target_fixture="response")
def open_home(observed):
    """Consulta ``GET /``."""
    return observed.http.get("/")


@then(parsers.parse("the response status is {status:d}"))
def status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then("the X-Request-ID header is a UUID")
def header_is_uuid(response):
    """Comprueba que el encabezado se generó como UUID."""
    assert uuid.UUID(response.headers["X-Request-ID"])


@then(parsers.parse('the X-Request-ID header is "{request_id}"'))
def header_is(response, request_id):
    """Comprueba que el encabezado conserva el id del cliente."""
    assert response.headers["X-Request-ID"] == request_id


@then("the response has an X-Request-ID header")
def header_is_present(response):
    """Comprueba que el encabezado viene aunque la petición sea inválida."""
    assert response.headers.get("X-Request-ID")


@then(parsers.parse('the response body has the request id "{request_id}"'))
def body_has_id(response, request_id):
    """Comprueba el campo ``request_id`` del cuerpo de ``/ask``."""
    assert response.json()["request_id"] == request_id


@then(parsers.parse('the log has the events "{names}" for "{request_id}"'))
def log_has_events(observed, names, request_id):
    """Comprueba los eventos de la petición, en orden; ``request`` va al final."""
    expected = [name.strip() for name in names.split(",")]
    events = events_for(observed, request_id)
    assert [event["event"] for event in events] == expected
    for event in events:
        assert event["service"] == "hr-assistant"
        assert "ts" in event


@then(parsers.parse('the audit event records the tool "{tool}" with its arguments'))
def audit_records_tool(observed, tool):
    """Comprueba el evento ``audit`` de la herramienta."""
    (audit,) = [event for event in observed.events() if event["event"] == "audit"]
    assert audit["action"] == "tool_call"
    assert audit["tool"] == tool
    assert audit["args"] == {"employee_id": "EMP-0004"}


@then("the agent_run event counts the question without its text")
def agent_run_counts(observed):
    """Comprueba los conteos de ``agent_run``."""
    (run,) = [event for event in observed.events() if event["event"] == "agent_run"]
    assert run["question_chars"] == len("¿Quién es EMP-0004?")
    assert run["history_messages"] == 0
    assert run["steps"] == 2
    assert run["tools"] == ["get_employee"]
    assert run["input_tokens"] > 0
    assert run["output_tokens"] > 0
    assert run["stopped"] is False


@then("no log line contains the question or the answer")
def no_personal_data(observed, response):
    """Busca el texto de la pregunta y de la respuesta en todo el log."""
    log = observed.log_stream.getvalue()
    assert "¿Quién es EMP-0004?" not in log
    assert response.json()["answer"] not in log
    assert "Jorge Díaz" not in log


@then(parsers.parse("the request event reports status {status:d}"))
def request_event_status(observed, response, status):
    """Comprueba el evento ``request`` de la petición."""
    request_id = response.headers["X-Request-ID"]
    request_event = events_for(observed, request_id)[-1]
    assert request_event["event"] == "request"
    assert request_event["method"] == "POST"
    assert request_event["path"] == "/ask"
    assert request_event["status"] == status
    assert request_event["latency_ms"] >= 0


@then(
    parsers.parse('the body is the internal error with the request id "{request_id}"')
)
def body_is_internal_error(response, request_id):
    """Comprueba el cuerpo genérico del 500."""
    assert response.json() == {"detail": "internal error", "request_id": request_id}


@then(
    parsers.parse('the error event reports the type "{error_type}" without the message')
)
def error_event_has_type(observed, exploding_model, error_type):
    """Comprueba que el log guarda el tipo de error, nunca su mensaje."""
    (error,) = [event for event in observed.events() if event["event"] == "error"]
    assert error["error_type"] == error_type
    assert exploding_model.SECRET_MESSAGE not in observed.log_stream.getvalue()


@then(parsers.parse("the metrics count at least {count:d} request"))
def metrics_count(metrics, count):
    """Comprueba el total de peticiones."""
    assert metrics["requests"] >= count


@then("the metrics have the latency percentiles p50 and p95")
def metrics_percentiles(metrics):
    """Comprueba los percentiles de latencia."""
    assert set(metrics["latency_ms"]) == {"p50", "p95"}
    assert metrics["latency_ms"]["p95"] >= metrics["latency_ms"]["p50"] > 0


@then("the metrics count input tokens")
def metrics_tokens(metrics):
    """Comprueba que se sumaron los tokens de la pregunta."""
    assert metrics["tokens"]["input"] > 0


@then("the SLO is met")
def slo_is_met(metrics):
    """Comprueba que el p95 está por debajo de la meta por omisión."""
    assert metrics["slo"] == {"p95_ms_target": 2000, "met": True}


@then("the SLO is not met")
def slo_is_not_met(metrics):
    """Comprueba que el p95 supera la meta."""
    assert metrics["slo"]["met"] is False


@then("the page shows the request id without using innerHTML")
def page_shows_request_id(observed, response):
    """Comprueba que el script de la página pinta el id con texto plano.

    La CSP no permite código en línea: la lógica vive en ``/static/app.js``.
    """
    script = observed.http.get("/static/app.js").text
    assert 'src="/static/app.js"' in response.text
    assert "request_id" in script
    assert "textContent" in script
    assert "innerHTML" not in response.text + script
