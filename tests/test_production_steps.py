"""Pasos de los escenarios de ``features/production.feature`` (sesión 4).

La API se arma con la clave de prueba de ``conftest.py`` y un reloj manual: el
límite de peticiones usa el mismo reloj inyectado, así que el escenario decide
cuándo pasa un minuto sin esperar.
"""

from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.config import ConfigError, load_settings

scenarios("features/production.feature")

INVALID_KEY_BODY = {"detail": "invalid or missing API key"}
RATE_LIMIT_BODY = {"detail": "rate limit exceeded"}
OPS_KEY = "ops-key-0123456789"
WEB_KEY = "web-key-0123456789"


@dataclass
class Running:
    """La API bajo prueba y el reloj que controla el escenario.

    :param observed: Cliente de la API con su log en memoria
    :type observed: ObservedClient
    :param clock: Reloj manual compartido por la API y el limitador
    :type clock: ManualClock
    """

    observed: object
    clock: object


@given(
    "the HR assistant is running and the client sends no API key",
    target_fixture="running",
)
def running_without_key(make_observed_client, manual_clock):
    """La API con la clave de prueba configurada; el cliente no la manda."""
    clock = manual_clock()
    return Running(make_observed_client(clock=clock, send_key=False), clock)


@given("the HR assistant is running with the test API key", target_fixture="running")
def running_with_key(make_observed_client, manual_clock):
    """La API con el cliente que manda la clave de prueba."""
    clock = manual_clock()
    return Running(make_observed_client(clock=clock), clock)


@given(
    parsers.parse(
        "the HR assistant is running with a limit of {limit:d} requests per minute"
    ),
    target_fixture="running",
)
def running_with_limit(make_observed_client, manual_clock, limit):
    """La API con ``RATE_LIMIT_PER_MINUTE`` bajo y el reloj detenido."""
    clock = manual_clock()
    observed = make_observed_client(
        clock=clock, env={"RATE_LIMIT_PER_MINUTE": str(limit)}
    )
    return Running(observed, clock)


@given(
    parsers.parse(
        'the HR assistant is running with clients "{first}" and "{second}" '
        "and a limit of {limit:d}"
    ),
    target_fixture="running",
)
def running_with_two_clients(make_observed_client, manual_clock, first, second, limit):
    """La API con dos clientes, cada uno con su clave, y el mismo límite."""
    clock = manual_clock()
    env = {
        "API_KEYS": f"{first}:{OPS_KEY},{second}:{WEB_KEY}",
        "RATE_LIMIT_PER_MINUTE": str(limit),
    }
    return Running(
        make_observed_client(clock=clock, env=env, send_key=False), clock
    )


@when(parsers.re(r'I ask "(?P<question>[^"]+)"$'), target_fixture="response")
def ask(running, question):
    """Manda una pregunta con los encabezados por omisión del cliente."""
    return running.observed.http.post("/ask", json={"question": question})


@when(
    parsers.re(r'I ask "(?P<question>[^"]+)" with the API key "(?P<key>[^"]+)"$'),
    target_fixture="response",
)
def ask_with_key(running, question, key):
    """Manda una pregunta con la clave indicada."""
    return running.observed.http.post(
        "/ask", json={"question": question}, headers={"X-API-Key": key}
    )


@when(
    parsers.parse('I read the metrics with the API key "{key}"'),
    target_fixture="metrics_response",
)
def read_metrics_with_key(running, key):
    """Consulta ``GET /metrics`` con la clave indicada."""
    return running.observed.http.get("/metrics", headers={"X-API-Key": key})


@when(
    parsers.re(r'I ask "(?P<question>[^"]+)" (?P<times>\d+) times in the same minute$'),
    target_fixture="responses",
)
def ask_many_times(running, question, times):
    """Manda la misma pregunta varias veces sin que avance el reloj."""
    return [
        running.observed.http.post("/ask", json={"question": question})
        for _ in range(int(times))
    ]


@when(parsers.parse("{seconds:d} seconds pass"))
def seconds_pass(running, seconds):
    """Avanza el reloj manual."""
    running.clock.advance(seconds)


@when(
    parsers.parse('"{first}" asks twice and then "{second}" asks once'),
    target_fixture="responses",
)
def two_clients_ask(running):
    """El primer cliente se pasa del límite; el segundo pregunta después."""
    question = {"question": "¿Quién es EMP-0004?"}
    http = running.observed.http
    return [
        http.post("/ask", json=question, headers={"X-API-Key": OPS_KEY}),
        http.post("/ask", json=question, headers={"X-API-Key": OPS_KEY}),
        http.post("/ask", json=question, headers={"X-API-Key": WEB_KEY}),
    ]


@when(
    "I load the settings for production with the fake model",
    target_fixture="config_error",
)
def load_production_with_fake_model():
    """Intenta cargar una configuración de producción con el modelo simulado."""
    env = {
        "ENVIRONMENT": "production",
        "MODEL_PROVIDER": "fake",
        "API_KEYS": "ops:" + OPS_KEY,
    }
    with pytest.raises(ConfigError) as error:
        load_settings(env)
    return error.value


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then("the body is exactly the invalid key message")
def body_is_invalid_key(response):
    """El 401 tiene el cuerpo exacto de la especificación."""
    assert response.json() == INVALID_KEY_BODY


@then("the response has an X-Request-ID header")
def header_is_present(response):
    """El 401 pasa por la observabilidad: trae su id de petición."""
    assert response.headers.get("X-Request-ID")


@then("the log has a request event with status 401 and no client")
def request_event_without_client(running, response):
    """El evento ``request`` del 401 existe y no tiene cliente."""
    request_id = response.headers["X-Request-ID"]
    (event,) = [
        event
        for event in running.observed.events()
        if event["event"] == "request" and event["request_id"] == request_id
    ]
    assert event["status"] == 401
    assert event["client"] is None


@then("both responses have status 401")
def both_are_401(response, metrics_response):
    """La clave equivocada no sirve ni para preguntar ni para las métricas."""
    assert response.status_code == 401
    assert metrics_response.status_code == 401
    assert metrics_response.json() == INVALID_KEY_BODY


@then(parsers.parse('"{home}", "{health}" and "{docs}" respond with status 200'))
def public_paths_answer(running, home, health, docs):
    """Las rutas públicas responden sin clave."""
    for path in (home, health, docs):
        assert running.observed.http.get(path).status_code == 200, path


@then("the request, agent_run and audit events name the test client")
def events_name_the_client(running):
    """Los tres eventos de la petición llevan el nombre del cliente."""
    events = running.observed.events()
    for name in ("request", "agent_run", "audit"):
        named = [event for event in events if event["event"] == name]
        assert named, name
        assert all(event["client"] == "unit-tests" for event in named), name


@then("no log line contains the API key")
def key_is_not_logged(running, api_key):
    """La clave no aparece en ninguna parte del log."""
    assert api_key not in running.observed.log_stream.getvalue()


@then("the last response status is 429 with the rate limit message")
def last_is_429(responses):
    """Las dos primeras pasan y la tercera se rechaza."""
    assert [response.status_code for response in responses] == [200, 200, 429]
    assert responses[-1].json() == RATE_LIMIT_BODY


@then("the Retry-After header is a positive number of seconds")
def retry_after_is_positive(responses):
    """``Retry-After`` dice cuántos segundos faltan."""
    assert int(responses[-1].headers["Retry-After"]) > 0


@then("the statuses are 200, 429 and 200")
def statuses_are(responses):
    """El segundo cliente no paga el límite del primero."""
    assert [response.status_code for response in responses] == [200, 429, 200]


@then(parsers.parse('the configuration fails naming "{variable}"'))
def configuration_fails(config_error, variable):
    """El error de arranque nombra la regla que falla."""
    assert variable in str(config_error)
