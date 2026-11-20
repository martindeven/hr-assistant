"""Pasos de los escenarios de ``features/multiagent.feature`` (reto, parte 1).

Los modelos se inyectan por nombre: el simulado para el camino feliz y uno que
siempre falla para el respaldo. Ninguna prueba toca la red ni espera.
"""

from pytest_bdd import given, parsers, scenarios, then, when

from app.llm import FakeModel, ModelUnavailableError

scenarios("features/multiagent.feature")


class FailingModel:
    """Modelo que siempre falla como lo haría el gateway tras sus reintentos.

    :param name: Nombre del modelo
    :type name: str
    """

    def __init__(self, name):
        self.name = name
        self.calls = 0
        self.last_messages = None

    def complete(self, messages):
        """Lanza siempre ``ModelUnavailableError``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :raises ModelUnavailableError: Siempre
        """
        self.calls += 1
        self.last_messages = messages
        raise ModelUnavailableError("gateway returned status 503")


def _split(text):
    """Convierte ``"a, b"`` en ``["a", "b"]``."""
    return [item.strip() for item in text.split(",")]


@given("the HR assistant is running with the fake model", target_fixture="observed")
def running_with_fake_model(make_observed_client):
    """La API con el modelo simulado y todos los permisos (desarrollo)."""
    return make_observed_client()


@given(
    "the HR assistant is running and the client sends no API key",
    target_fixture="observed",
)
def running_without_key(make_observed_client):
    """La API con la clave configurada; el cliente no la manda."""
    return make_observed_client(send_key=False)


@given(
    "the HR assistant is running with a large model that always fails",
    target_fixture="observed",
)
def running_with_failing_large_model(make_observed_client, settings, directory):
    """El modelo grande falla siempre; el pequeño es el simulado."""
    models = {
        settings.model_small: FakeModel(
            directory.departments(), name=settings.model_small
        ),
        settings.model_large: FailingModel(settings.model_large),
    }
    return make_observed_client(models=models)


@given(
    "the HR assistant is running with two models that always fail",
    target_fixture="observed",
)
def running_with_failing_models(make_observed_client, settings):
    """Los dos modelos fallan siempre."""
    models = {
        settings.model_small: FailingModel(settings.model_small),
        settings.model_large: FailingModel(settings.model_large),
    }
    return make_observed_client(models=models)


@when(parsers.parse('I ask "{question}"'), target_fixture="response")
def ask(observed, question):
    """Manda una pregunta sin historial."""
    return observed.http.post("/ask", json={"question": question})


@when("I check the readiness", target_fixture="response")
def check_readiness(observed):
    """Consulta ``GET /ready`` sin clave."""
    return observed.http.get("/ready")


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then(parsers.parse('the agents are "{agents}"'))
def agents_are(response, agents):
    """Los agentes que corrieron, en orden."""
    assert response.json()["agents"] == _split(agents)


@then(parsers.parse('the tools used are "{tools}"'))
def tools_are(response, tools):
    """Las herramientas de todos los especialistas, en orden."""
    assert response.json()["tools_used"] == _split(tools)


@then(parsers.parse('the answer contains "{text}"'))
def answer_contains(response, text):
    """La respuesta trae el texto indicado."""
    assert text in response.json()["answer"]


@then(parsers.parse('the review is "{review}"'))
def review_is(response, review):
    """El resultado del revisor."""
    assert response.json()["guardrails"]["review"] == review


@then(parsers.parse("the steps add up to {steps:d}"))
def steps_add_up(response, steps):
    """Los pasos de los dos especialistas se suman."""
    assert response.json()["steps"] == steps


@then("the answer did not use the fallback")
def no_fallback(response):
    """Una respuesta normal trae ``fallback: false``."""
    assert response.json()["fallback"] is False


@then("the answer used the fallback")
def used_fallback(response):
    """La respuesta del respaldo trae ``fallback: true``."""
    assert response.json()["fallback"] is True


@then("the answer reports the small model")
def reports_small_model(response, settings):
    """``model`` dice el modelo que respondió de verdad."""
    assert response.json()["model"] == settings.model_small


@then(parsers.parse('the log has an audit event "{action}"'))
def audit_event_exists(observed, action):
    """Existe un evento ``audit`` con esa acción, cliente y resultado."""
    audits = [
        event
        for event in observed.events()
        if event["event"] == "audit" and event.get("action") == action
    ]
    assert audits
    assert {"client", "request_id", "outcome"} <= set(audits[0])


@then("the readiness reports ready with its checks")
def readiness_reports_ready(response):
    """``/ready`` dice qué se cargó: directorio, políticas y precios."""
    body = response.json()
    assert body["status"] == "ready"
    assert {"directory", "policies", "prices"} <= set(body["checks"])
