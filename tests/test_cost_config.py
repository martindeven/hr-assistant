"""Pruebas de la configuración nueva de la sesión 3 (costos)."""

import pytest

from app.config import ConfigError, load_settings

# Token de prueba armado en tiempo de ejecución: no es un secreto (gitleaks).
FAKE_TOKEN = "-".join(["gateway", "test"] * 3)
CLOUDFLARE_ENV = {
    "MODEL_PROVIDER": "gateway",
    "CF_ACCOUNT_ID": "acc123",
    "CF_GATEWAY_ID": "gw456",
    "CF_API_TOKEN": FAKE_TOKEN,
}


def test_defaults_match_the_spec_table():
    settings = load_settings({})

    assert settings.cf_account_id == ""
    assert settings.cf_gateway_id == "default"
    assert settings.cf_api_token == ""
    assert settings.model_small == "@cf/meta/llama-3.1-8b-instruct-fast"
    assert settings.model_large == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert settings.routing is True
    assert settings.router_max_chars == 80
    assert settings.history_window == 6
    assert settings.model_max_tokens == 300
    assert settings.model_temperature == 0
    assert settings.cache_ttl_seconds == 0
    assert settings.prices_path.parts[-2:] == ("data", "model_prices.json")


def test_gateway_without_any_cloudflare_variable_fails_to_start():
    assert load_settings(CLOUDFLARE_ENV).model_provider == "gateway"
    for name in ("CF_ACCOUNT_ID", "CF_GATEWAY_ID", "CF_API_TOKEN"):
        with pytest.raises(ConfigError):
            load_settings({**CLOUDFLARE_ENV, name: ""})


def test_invalid_cost_settings_are_rejected_but_a_zero_window_is_valid():
    assert load_settings({"HISTORY_WINDOW": "0"}).history_window == 0
    invalid_values = [
        {"HISTORY_WINDOW": "-1"},
        {"MODEL_TEMPERATURE": "2.5"},
        {"MODEL_SMALL": "@cf/unknown/model-without-price"},
    ]
    for env in invalid_values:
        with pytest.raises(ConfigError):
            load_settings(env)


def test_settings_repr_does_not_show_the_api_token():
    settings = load_settings(CLOUDFLARE_ENV)

    assert FAKE_TOKEN not in repr(settings)
