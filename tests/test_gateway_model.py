"""Pruebas del modelo real a través de AI Gateway, sin tocar la red.

Cada prueba inyecta un ``httpx.Client`` con ``httpx.MockTransport``: el
manejador guarda la petición y devuelve la respuesta que se le indique.
"""

import json

import httpx
import pytest

from app.config import load_settings
from app.directory import EmployeeDirectory
from app.llm import FakeModel, GatewayModel, ModelUnavailableError, build_models

# Token de prueba armado en tiempo de ejecución: no es un secreto (gitleaks).
SECRET_TOKEN = "-".join(["gateway", "test"] * 3)
GATEWAY_ENV = {
    "MODEL_PROVIDER": "gateway",
    "CF_ACCOUNT_ID": "acc123",
    "CF_GATEWAY_ID": "gw456",
    "CF_API_TOKEN": SECRET_TOKEN,
}
SMALL = "@cf/meta/llama-3.1-8b-instruct-fast"
EXPECTED_URL = (
    "https://gateway.ai.cloudflare.com/v1/acc123/gw456/workers-ai/v1/chat/completions"
)
QUESTION = [{"role": "user", "content": "¿Quién es EMP-0004?"}]


def ok_body(content='{"answer": "Hola"}'):
    """Cuerpo de una respuesta correcta del gateway."""
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7},
    }


def gateway_model(handler, extra_env=None):
    """Crea un ``GatewayModel`` cuyo cliente HTTP responde con ``handler``."""
    settings = load_settings({**GATEWAY_ENV, **(extra_env or {})})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GatewayModel(name=SMALL, settings=settings, http_client=client)


def recording_handler(requests, response):
    """Manejador que guarda cada petición y devuelve siempre ``response``."""

    def handler(request):
        requests.append(request)
        return response

    return handler


def test_gateway_call_sends_url_token_and_body_and_reads_usage():
    requests = []
    response = httpx.Response(200, json=ok_body())

    completion = gateway_model(recording_handler(requests, response)).complete(QUESTION)

    request = requests[0]
    body = json.loads(request.content)
    assert str(request.url) == EXPECTED_URL
    assert request.headers["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert set(body) == {"model", "messages", "max_tokens", "temperature"}
    assert (body["model"], body["max_tokens"], body["temperature"]) == (SMALL, 300, 0)
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == QUESTION[-1]
    assert (completion.input_tokens, completion.output_tokens) == (42, 7)
    assert completion.content == '{"answer": "Hola"}'
    assert completion.cached is False


def test_cache_ttl_header_is_sent_only_when_the_ttl_is_positive():
    requests = []
    response = httpx.Response(200, json=ok_body())

    gateway_model(recording_handler(requests, response)).complete(QUESTION)
    gateway_model(
        recording_handler(requests, response), {"CACHE_TTL_SECONDS": "60"}
    ).complete(QUESTION)

    assert "cf-aig-cache-ttl" not in requests[0].headers
    assert requests[1].headers["cf-aig-cache-ttl"] == "60"


def test_a_cache_hit_marks_the_completion_as_cached():
    response = httpx.Response(
        200, json=ok_body(), headers={"cf-aig-cache-status": "hit"}
    )
    model = gateway_model(
        recording_handler([], response), {"CACHE_TTL_SECONDS": "60"}
    )

    assert model.complete(QUESTION).cached is True


def test_code_fences_are_removed_from_the_reply():
    fenced = '```json\n{"answer": "Hola"}\n```'
    response = httpx.Response(200, json=ok_body(fenced))

    completion = gateway_model(recording_handler([], response)).complete(QUESTION)

    assert json.loads(completion.content) == {"answer": "Hola"}


def test_gateway_failures_raise_model_unavailable_without_the_token():
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    failures = {
        "status 500": recording_handler([], httpx.Response(500, text=SECRET_TOKEN)),
        "timeout": timeout,
        "missing usage": recording_handler(
            [], httpx.Response(200, json={"choices": ok_body()["choices"]})
        ),
    }
    for case, handler in failures.items():
        with pytest.raises(ModelUnavailableError) as error:
            gateway_model(handler).complete(QUESTION)
        assert SECRET_TOKEN not in str(error.value), case


def test_fake_provider_builds_one_fake_model_per_configured_name():
    settings = load_settings({})
    directory = EmployeeDirectory(settings.employees_path)

    models = build_models(settings, directory)

    assert set(models) == {settings.model_small, settings.model_large}
    for name, model in models.items():
        assert isinstance(model, FakeModel)
        assert model.name == name
