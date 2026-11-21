"""Pasos de ``features/cost_levers.feature`` (reto, parte 2, puntos 6 y 7).

Los modelos cuentan sus llamadas para probar que un acierto de la caché no
llama al modelo. El reloj es inyectado: la prueba del presupuesto mueve la
hora UTC sin esperar.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.llm import Completion, FakeModel, ModelUnavailableError

scenarios("features/cost_levers.feature")

# Clave del kiosco armada en tiempo de ejecución: no es un secreto (gitleaks).
KIOSK_KEY = "-".join(["kiosk", "test"] * 3)
TEST_CLIENT = "unit-tests"
KIOSK_SCOPES = f"{TEST_CLIENT}:directory policies metrics,kiosk:policies"
CACHE_ON = {"RESPONSE_CACHE_TTL_SECONDS": "300"}
BEFORE_MIDNIGHT = datetime(2026, 1, 15, 23, 59, 0, tzinfo=timezone.utc)
UNKNOWN_EMPLOYEE_ANSWER = json.dumps({"answer": "El director general es EMP-0099."})


class CountingModel:
    """Delega en otro modelo y cuenta las llamadas.

    :param model: Modelo que responde de verdad
    :type model: app.llm.LanguageModel
    :param cached: Si cada respuesta se marca como servida por el gateway
    :type cached: bool
    """

    def __init__(self, model, cached=False):
        self._model = model
        self._cached = cached
        self.calls = 0

    def complete(self, messages):
        """Cuenta la llamada y responde con el modelo envuelto.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: La respuesta del modelo envuelto
        :rtype: app.llm.Completion
        """
        self.calls += 1
        completion = self._model.complete(messages)
        if self._cached:
            return replace(completion, cached=True)
        return completion


class UnknownEmployeeModel:
    """Modelo que siempre nombra a un empleado que no existe.

    El reviewer reemplaza esa respuesta (``review: replaced``).
    """

    def complete(self, messages):
        """Responde siempre con ``EMP-0099``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: La respuesta fija
        :rtype: app.llm.Completion
        """
        del messages
        return Completion(UNKNOWN_EMPLOYEE_ANSWER, input_tokens=10, output_tokens=2)


class FailingModel:
    """Modelo que siempre falla como el gateway tras sus reintentos."""

    def complete(self, messages):
        """Lanza siempre ``ModelUnavailableError``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :raises ModelUnavailableError: Siempre
        """
        del messages
        raise ModelUnavailableError("gateway returned status 503")


@dataclass
class World:
    """Lo que comparte un escenario: cliente, modelo que cuenta y respuestas.

    :param observed: Cliente de la API y su log
    :type observed: tests.conftest.ObservedClient
    :param model: Modelo que cuenta sus llamadas
    :type model: CountingModel
    :param clock: Reloj inyectado, o ``None``
    :type clock: object
    :param responses: Respuestas de ``POST /ask``, en orden
    :type responses: list
    :param calls_after: Llamadas al modelo después de cada respuesta
    :type calls_after: list[int]
    """

    observed: object
    model: CountingModel
    clock: object = None
    responses: list = field(default_factory=list)
    calls_after: list = field(default_factory=list)


@pytest.fixture
def fake_model(directory):
    """Un modelo simulado nuevo."""
    return FakeModel(directory.departments())


@given(
    "the HR assistant is running with the response cache on", target_fixture="world"
)
def running_with_cache(make_observed_client, fake_model):
    """La caché de respuestas encendida con el modelo simulado."""
    model = CountingModel(fake_model)
    return World(make_observed_client(model=model, env=CACHE_ON), model)


@given(
    "the HR assistant is running with the response cache on for two clients",
    target_fixture="world",
)
def running_with_cache_and_kiosk(make_observed_client, fake_model, api_key):
    """La caché encendida, el cliente completo y un kiosco de políticas."""
    model = CountingModel(fake_model)
    env = {
        **CACHE_ON,
        "API_KEYS": f"{TEST_CLIENT}:{api_key},kiosk:{KIOSK_KEY}",
        "API_KEY_SCOPES": KIOSK_SCOPES,
    }
    return World(make_observed_client(model=model, env=env), model)


@given(
    parsers.parse(
        "the HR assistant is running with the response cache on and {setup}"
    ),
    target_fixture="world",
)
def running_with_cache_and_setup(make_observed_client, fake_model, settings, setup):
    """La caché encendida con un caso cuya respuesta no se debe guardar."""
    if setup == "a one step limit":
        model = CountingModel(fake_model)
        env = {**CACHE_ON, "AGENT_MAX_STEPS": "1"}
        return World(make_observed_client(model=model, env=env), model)
    if setup == "a model that names an unknown employee":
        model = CountingModel(UnknownEmployeeModel())
        return World(make_observed_client(model=model, env=CACHE_ON), model)
    assert setup == "a large model that always fails", setup
    model = CountingModel(fake_model)
    models = {settings.model_small: model, settings.model_large: FailingModel()}
    return World(make_observed_client(models=models, env=CACHE_ON), model)


@given(
    "the HR assistant is running with a tiny daily budget at 23:59:00 UTC",
    target_fixture="world",
)
def running_with_tiny_budget(make_observed_client, fake_model, utc_clock):
    """Un tope diario mínimo: la primera respuesta ya lo alcanza."""
    clock = utc_clock(BEFORE_MIDNIGHT)
    model = CountingModel(fake_model)
    observed = make_observed_client(
        model=model, clock=clock, env={"CLIENT_DAILY_BUDGET_USD": "0.0000001"}
    )
    return World(observed, model, clock=clock)


@given(
    "the HR assistant is running with a model served from the gateway cache",
    target_fixture="world",
)
def running_with_gateway_cache(make_observed_client, fake_model):
    """Cada llamada al modelo viene de la caché del gateway."""
    model = CountingModel(fake_model, cached=True)
    return World(make_observed_client(model=model), model)


def _record(world, response):
    """Guarda la respuesta y cuántas llamadas al modelo había después de ella."""
    world.responses.append(response)
    world.calls_after.append(world.model.calls)
    return response


@when(parsers.parse('I ask "{question}"'))
def ask(world, question):
    """Manda una pregunta con la clave de pruebas."""
    _record(world, world.observed.http.post("/ask", json={"question": question}))


@when(parsers.parse('the kiosk asks "{question}"'))
def kiosk_asks(world, question):
    """Manda una pregunta con la clave del kiosco."""
    response = world.observed.http.post(
        "/ask", json={"question": question}, headers={"X-API-Key": KIOSK_KEY}
    )
    _record(world, response)


@when("the clock moves to the next day")
def next_day(world):
    """La hora UTC pasa la medianoche."""
    world.clock.now = BEFORE_MIDNIGHT + timedelta(minutes=2)


@then(parsers.parse('the first answer has cache "{cache}"'))
def first_answer_cache(world, cache):
    """De dónde vino la primera respuesta."""
    assert world.responses[0].status_code == 200
    assert world.responses[0].json()["cache"] == cache


@then(parsers.parse('the last answer has cache "{cache}"'))
def last_answer_cache(world, cache):
    """De dónde vino la última respuesta."""
    assert world.responses[-1].status_code == 200
    assert world.responses[-1].json()["cache"] == cache


@then('the last answer has cache "response", cost 0 and 0 steps')
def last_answer_from_response_cache(world):
    """Un acierto no cuesta ni llama al modelo."""
    body = world.responses[-1].json()
    assert body["cache"] == "response"
    assert body["cost_usd"] == 0
    assert body["steps"] == 0
    assert body["tools_used"] == []


@then("both answers have the same text but different request ids")
def same_text_other_request_id(world):
    """El acierto trae la misma respuesta con su propio ``request_id``."""
    first, second = (response.json() for response in world.responses)
    assert second["answer"] == first["answer"]
    assert second["request_id"] != first["request_id"]


@then("the model was called only for the first answer")
def model_called_once(world):
    """La segunda respuesta no llamó al modelo."""
    assert world.calls_after[0] > 0
    assert world.calls_after[1] == world.calls_after[0]


@then("the model was called for both answers")
def model_called_twice(world):
    """La segunda respuesta volvió a llamar al modelo."""
    assert 0 < world.calls_after[0] < world.calls_after[1]


@then(parsers.parse("every answer has status {status:d}"))
def every_answer_status(world, status):
    """Todas las respuestas tienen ese código."""
    assert [response.status_code for response in world.responses] == [
        status
    ] * len(world.responses)


@then("the model was never called")
def model_never_called(world):
    """Una pregunta bloqueada no llega al modelo."""
    assert world.model.calls == 0


@then(parsers.parse('the last agents are "{agents}"'))
def last_agents(world, agents):
    """Los agentes que corrieron en la última respuesta."""
    expected = [agent.strip() for agent in agents.split(",")]
    assert world.responses[-1].json()["agents"] == expected


@then(parsers.parse("the first answer has status {status:d}"))
def first_answer_status(world, status):
    """El código de la primera respuesta."""
    assert world.responses[0].status_code == status


@then(parsers.parse("the last answer has status {status:d}"))
def last_answer_status(world, status):
    """El código de la última respuesta."""
    assert world.responses[-1].status_code == status


@then(
    parsers.parse(
        'the last answer is a 429 "{detail}" with Retry-After {seconds:d}'
    )
)
def last_answer_over_budget(world, detail, seconds):
    """El tope diario responde ``429`` con los segundos hasta la medianoche UTC."""
    response = world.responses[-1]
    assert response.status_code == 429
    assert response.json()["detail"] == detail
    assert response.headers["Retry-After"] == str(seconds)


@then(parsers.parse('the log has an audit event "{action}"'))
def audit_event_exists(world, action):
    """Existe un evento ``audit`` con esa acción y el cliente."""
    audits = [
        event
        for event in world.observed.events()
        if event["event"] == "audit" and event.get("action") == action
    ]
    assert audits
    assert audits[0]["client"] == TEST_CLIENT
