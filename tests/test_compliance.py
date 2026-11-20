"""Pruebas de las reglas de compliance que se aplican con ``ENVIRONMENT=production``.

Se prueban a través de ``load_settings``: es lo que corre al arrancar.
"""

import pytest

from app.config import ConfigError, load_settings

PROD_KEY = "prod-key-0123456789"
SHORT_KEY = "abcdefghij12345"
SAFE_PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "MODEL_PROVIDER": "gateway",
    "CF_ACCOUNT_ID": "acc123",
    "CF_API_TOKEN": "cf-token-not-real",
    "API_KEYS": f"ops:{PROD_KEY}",
    # Regla 3.7 de la spec 05: en producción cada cliente necesita alcances.
    "API_KEY_SCOPES": "ops:directory policies metrics",
}
TRACING_ON = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "lsv2-not-real"}


@pytest.mark.parametrize(
    ("overrides", "expected_text"),
    [
        ({"MODEL_PROVIDER": "fake"}, "MODEL_PROVIDER"),
        ({"API_KEYS": ""}, "API_KEYS"),
        ({"API_KEYS": f"ops:{SHORT_KEY}"}, "15"),
        ({**TRACING_ON, "HIDE_INPUTS": "false"}, "HIDE_INPUTS"),
        ({**TRACING_ON, "HIDE_OUTPUTS": "false"}, "HIDE_OUTPUTS"),
    ],
    ids=[
        "fake_model",
        "no_api_keys",
        "key_shorter_than_16",
        "traces_show_inputs",
        "traces_show_outputs",
    ],
)
def test_each_unsafe_production_setting_refuses_to_start(overrides, expected_text):
    with pytest.raises(ConfigError) as error:
        load_settings({**SAFE_PRODUCTION_ENV, **overrides})

    assert expected_text in str(error.value)
    assert SHORT_KEY not in str(error.value)


def test_all_failing_rules_are_reported_together_without_any_key():
    env = {
        **SAFE_PRODUCTION_ENV,
        **TRACING_ON,
        "MODEL_PROVIDER": "fake",
        "API_KEYS": f"ops:{SHORT_KEY}",
        "HIDE_OUTPUTS": "false",
    }

    with pytest.raises(ConfigError) as error:
        load_settings(env)

    message = str(error.value)
    for expected in ("MODEL_PROVIDER", "ops", "15", "HIDE_OUTPUTS"):
        assert expected in message
    assert SHORT_KEY not in message


def test_compliance_applies_only_in_production():
    safe = load_settings({**SAFE_PRODUCTION_ENV, **TRACING_ON})
    development = load_settings(
        {"MODEL_PROVIDER": "fake", "API_KEYS": f"ops:{SHORT_KEY}"}
    )

    assert safe.environment == "production"
    assert development.environment == "development"
