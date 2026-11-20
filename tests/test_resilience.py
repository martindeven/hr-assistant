"""Pruebas de resiliencia del modelo real y de la configuración nueva (punto 2).

``GatewayModel`` recibe un ``sleep`` falso que anota las esperas: ninguna
prueba espera de verdad ni toca la red (``httpx.MockTransport``).
"""

import json

import httpx
import pytest

from app.config import ConfigError, load_settings
from app.llm import GatewayModel, ModelUnavailableError

GATEWAY_ENV = {
    "MODEL_PROVIDER": "gateway",
    "CF_ACCOUNT_ID": "acc123",
    "CF_GATEWAY_ID": "gw456",
    "CF_API_TOKEN": "secret-token-123",
}
LARGE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
QUESTION = [{"role": "user", "content": "¿Quién es EMP-0004?"}]
OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": '{"answer": "Hola"}'}}],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
}


def scripted_gateway(outcomes, requests, env=None):
    """Crea un ``GatewayModel`` cuyo gateway responde ``outcomes`` en orden.

    Cada resultado es un código HTTP o una excepción de ``httpx`` a lanzar.

    :return: El modelo y la lista de esperas que pidió
    :rtype: tuple[GatewayModel, list[float]]
    """
    pending = list(outcomes)

    def handler(request):
        requests.append(request)
        outcome = pending.pop(0)
        if isinstance(outcome, type) and issubclass(outcome, Exception):
            raise outcome("simulated failure", request=request)
        body = OK_BODY if outcome == 200 else {"error": "x"}
        return httpx.Response(outcome, json=body)

    sleeps = []
    settings = load_settings({**GATEWAY_ENV, **(env or {})})
    model = GatewayModel(
        name=LARGE,
        settings=settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    return model, sleeps


def test_transient_failures_are_retried_with_a_doubling_wait():
    for outcomes in (
        [httpx.ReadTimeout, 429, 200],
        [httpx.ConnectError, 503, 200],
    ):
        requests = []
        model, sleeps = scripted_gateway(outcomes, requests)

        completion = model.complete(QUESTION)

        assert json.loads(completion.content) == {"answer": "Hola"}
        assert len(requests) == 3
        assert sleeps == pytest.approx([0.2, 0.4])


def test_retries_stop_after_model_retries_and_never_happen_on_other_4xx():
    requests = []
    model, _ = scripted_gateway([500, 500, 500, 200], requests)
    with pytest.raises(ModelUnavailableError):
        model.complete(QUESTION)
    assert len(requests) == 3

    for status in (400, 401, 404):
        requests = []
        model, sleeps = scripted_gateway([status, 200], requests)
        with pytest.raises(ModelUnavailableError):
            model.complete(QUESTION)
        assert (len(requests), sleeps) == (1, [])


def test_the_gateway_uses_the_configured_timeout_and_the_specialist_prompt():
    requests = []
    model, _ = scripted_gateway(
        [200], requests, env={"MODEL_TIMEOUT_SECONDS": "7"}
    )
    system = {"role": "system", "content": "Prompt del especialista de políticas"}

    model.complete([system, *QUESTION])

    sent = json.loads(requests[0].content)["messages"]
    assert [message["role"] for message in sent].count("system") == 1
    assert sent[0] == system
    assert requests[0].extensions["timeout"]["read"] == 7


def test_new_settings_have_the_spec_defaults_and_reject_bad_values():
    settings = load_settings({})

    assert settings.policies_path.name == "policies.json"
    assert (settings.model_retries, settings.model_retry_backoff_ms) == (2, 200)
    assert settings.model_fallback is True
    assert settings.model_timeout_seconds == 30
    assert (settings.pii_input_mode, settings.injection_guard) == ("redact", True)
    assert (settings.max_answer_chars, settings.max_body_bytes) == (1500, 65536)
    for name, value in (
        ("PII_INPUT_MODE", "maybe"),
        ("INJECTION_GUARD", "yes"),
        ("MODEL_RETRIES", "-1"),
        ("MODEL_TIMEOUT_SECONDS", "0"),
        ("POLICIES_PATH", "data/missing.json"),
    ):
        with pytest.raises(ConfigError, match=name):
            load_settings({name: value})
