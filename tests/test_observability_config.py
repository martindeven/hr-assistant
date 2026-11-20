"""Configuración nueva de la observabilidad y los costos (reto, parte 2)."""

import pytest

from app.config import ConfigError, load_settings


def test_new_settings_default_to_the_spec_table():
    settings = load_settings({})

    assert settings.langsmith_project == "hr-assistant"
    assert settings.response_cache_ttl_seconds == 0
    assert settings.response_cache_max_entries == 512
    assert settings.client_daily_budget_usd == 0
    assert settings.alert_error_rate == 0.05
    assert settings.alert_cost_per_request_usd == 0.001
    assert settings.alert_guardrail_events == 10


def test_invalid_cost_and_alert_settings_stop_the_start():
    valid = load_settings(
        {"RESPONSE_CACHE_TTL_SECONDS": "300", "CLIENT_DAILY_BUDGET_USD": "0.5"}
    )
    assert valid.response_cache_ttl_seconds == 300
    assert valid.client_daily_budget_usd == 0.5

    invalid = {
        "LANGSMITH_PROJECT": "",
        "RESPONSE_CACHE_TTL_SECONDS": "-1",
        "RESPONSE_CACHE_MAX_ENTRIES": "0",
        "CLIENT_DAILY_BUDGET_USD": "-0.5",
        "ALERT_ERROR_RATE": "mucho",
        "ALERT_COST_PER_REQUEST_USD": "inf",
        "ALERT_GUARDRAIL_EVENTS": "diez",
    }
    for name, value in invalid.items():
        with pytest.raises(ConfigError) as error:
            load_settings({name: value})
        assert name in str(error.value), name
