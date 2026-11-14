"""Pasos de los escenarios de ``features/costs.feature`` (sesión 3).

Los modelos se inyectan con ``create_app(models={nombre: modelo})``: así cada
escenario sabe qué modelo se usó, qué mensajes recibió y si la llamada fue
cacheada, sin red ni hora real.
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from app.config import load_settings
from app.llm import Completion, FakeModel, ModelUnavailableError
from app.main import create_app
from app.tracing import Tracer

scenarios("features/costs.feature")

FIXED_REQUEST_ID = "req-costs-1"


class RecordingModel:
    """Envuelve un modelo y guarda los mensajes de cada llamada.

    :param inner: Modelo que responde de verdad
    :type inner: app.llm.LanguageModel
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def complete(self, messages):
        """Guarda una copia de los mensajes y delega en el modelo interno.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: La respuesta del modelo interno
        :rtype: app.llm.Completion
        """
        self.calls.append([dict(message) for message in messages])
        return self._inner.complete(messages)


class UnavailableModel:
    """Modelo que siempre falla como un gateway caído."""

    def complete(self, messages):
        """Lanza siempre ``ModelUnavailableError``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :raises ModelUnavailableError: Siempre
        """
        raise ModelUnavailableError("gateway returned 503")


class CachedModel:
    """Modelo cuya única llamada llega cacheada del gateway (``HIT``)."""

    def complete(self, messages):
        """Responde directo, con 10 tokens de entrada, 2 de salida y ``cached``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: Una respuesta final cacheada
        :rtype: app.llm.Completion
        """
        del messages  # La respuesta cacheada no depende de la conversación.
        return Completion(
            content=json.dumps({"answer": "Respuesta guardada."}),
            input_tokens=10,
            output_tokens=2,
            cached=True,
        )


class CostApp:
    """La API bajo prueba, sus modelos y el log que escribe.

    :param settings: Configuración usada
    :type settings: app.config.Settings
    :param models: Modelos inyectados por nombre
    :type models: dict
    """

    def __init__(self, settings, models, directory, clock, api_key):
        self.settings = settings
        self.models = models
        self.log_stream = io.StringIO()
        app = create_app(
            settings=settings,
            models=models,
            directory=directory,
            clock=clock,
            log_stream=self.log_stream,
            tracer=Tracer(enabled=False),
            new_id=lambda: FIXED_REQUEST_ID,
        )
        # Desde la sesión 4, ``/ask`` y ``/metrics`` piden la clave.
        self.http = TestClient(app, headers={"X-API-Key": api_key})

    def events(self):
        """Lee el log como una lista de eventos.

        :return: Un diccionario por línea del log
        :rtype: list[dict]
        """
        return [json.loads(line) for line in self.log_stream.getvalue().splitlines()]


@pytest.fixture
def build_api(directory, fake_clock, keyed_env, api_key):
    """Fabrica la API con la clave de prueba y un modelo por nombre."""

    def _build(env, make_model):
        """Arma la API con un modelo por cada nombre de la configuración.

        :param env: Variables propias del escenario
        :type env: dict
        :param make_model: Función que recibe el nombre y devuelve el modelo
        :type make_model: collections.abc.Callable[[str], object]
        :return: La API lista
        :rtype: CostApp
        """
        settings = load_settings(keyed_env(env))
        models = {
            name: make_model(name)
            for name in (settings.model_small, settings.model_large)
        }
        return CostApp(settings, models, directory, fake_clock(), api_key)

    return _build


@given("the HR assistant is running with priced fake models", target_fixture="api")
def priced_fake_models(build_api, directory):
    """Dos modelos simulados con los nombres de ``MODEL_SMALL`` y ``MODEL_LARGE``."""
    return build_api(
        {},
        lambda name: RecordingModel(FakeModel(directory.departments(), name=name)),
    )


@given(
    parsers.parse(
        "the HR assistant is running with priced fake models and a history "
        "window of {window:d}"
    ),
    target_fixture="api",
)
def priced_fake_models_with_window(build_api, directory, window):
    """Igual que el anterior, con ``HISTORY_WINDOW`` fijado."""
    return build_api(
        {"HISTORY_WINDOW": str(window)},
        lambda name: RecordingModel(FakeModel(directory.departments(), name=name)),
    )


@given(
    "the HR assistant is running with a model that is unavailable",
    target_fixture="api",
)
def unavailable_model(build_api):
    """Los dos nombres apuntan a un modelo que siempre falla."""
    return build_api({}, lambda name: UnavailableModel())


@given(
    "the HR assistant is running with a model whose calls are cached",
    target_fixture="api",
)
def cached_model(build_api):
    """Los dos nombres apuntan a un modelo que responde desde la caché."""
    return build_api({}, lambda name: CachedModel())


@when(parsers.re(r'I ask "(?P<question>[^"]+)"$'), target_fixture="response")
def ask(api, question):
    """Manda una pregunta sin historial."""
    return api.http.post("/ask", json={"question": question})


@when(
    parsers.parse('I send "{question}" with {count:d} messages of history'),
    target_fixture="response",
)
def ask_with_long_history(api, question, count):
    """Manda una pregunta con ``count`` mensajes que alternan usuario y asistente."""
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Mensaje anterior {index}",
        }
        for index in range(count)
    ]
    return api.http.post("/ask", json={"question": question, "history": history})


@when(
    parsers.parse('I ask "{first}" and then "{second}"'),
    target_fixture="responses",
)
def ask_twice(api, first, second):
    """Manda dos preguntas seguidas."""
    return [
        api.http.post("/ask", json={"question": question}).json()
        for question in (first, second)
    ]


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then("the model used is the small model")
def model_is_small(api, response):
    """El campo ``model`` es el de ``MODEL_SMALL``."""
    assert response.json()["model"] == api.settings.model_small


@then("the model used is the large model")
def model_is_large(api, response):
    """El campo ``model`` es el de ``MODEL_LARGE``."""
    assert response.json()["model"] == api.settings.model_large


@then(parsers.parse('the route is "{route}"'))
def route_is(response, route):
    """El campo ``route`` dice el motivo del enrutador."""
    assert response.json()["route"] == route


@then("the cost is greater than zero")
def cost_is_positive(response):
    """El costo no se redondea a cero con el modelo simulado."""
    assert response.json()["cost_usd"] > 0


@then("the cost is zero")
def cost_is_zero(response):
    """Una respuesta que solo tuvo llamadas cacheadas no cuesta."""
    assert response.json()["cost_usd"] == 0


@then(parsers.parse("the cached calls are {count:d}"))
def cached_calls_are(response, count):
    """El campo ``cached_calls`` cuenta las llamadas con ``HIT``."""
    assert response.json()["cached_calls"] == count


@then(parsers.parse("the model first receives {count:d} messages"))
def model_first_receives(api, count):
    """La primera llamada al modelo lleva la ventana más la pregunta actual."""
    small = api.models[api.settings.model_small]
    first_call = small.calls[0]
    assert len(first_call) == count
    assert first_call[-1] == {"role": "user", "content": "¿Quién es EMP-0004?"}


@then("the metrics cost is the sum of both answer costs")
def metrics_cost_is_the_sum(api, responses):
    """``/metrics`` acumula el ``cost_usd`` de cada respuesta."""
    expected = sum(body["cost_usd"] for body in responses)
    metrics = api.http.get("/metrics").json()
    assert expected > 0
    assert metrics["cost_usd"] == pytest.approx(expected)


@then(
    parsers.parse('the body says "{detail}" with the request id of the header')
)
def body_says_detail(response, detail):
    """El cuerpo del 502 trae el detalle y el mismo id del encabezado."""
    assert response.json() == {"detail": detail, "request_id": FIXED_REQUEST_ID}
    assert response.headers["X-Request-ID"] == FIXED_REQUEST_ID


@then(parsers.parse('the log has an error event of type "{error_type}"'))
def log_has_error_event(api, error_type):
    """El log guarda el tipo de error, ligado a la petición."""
    errors = [event for event in api.events() if event.get("event") == "error"]
    assert errors
    assert errors[0]["error_type"] == error_type
    assert errors[0]["request_id"] == FIXED_REQUEST_ID
