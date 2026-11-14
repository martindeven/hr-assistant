"""Pruebas de la configuración nueva de la sesión 4 (producción)."""

import pytest

from app.config import ConfigError, load_settings


def test_production_settings_default_to_development_without_keys():
    settings = load_settings({})

    assert settings.environment == "development"
    assert isinstance(settings.api_keys, dict)
    assert len(settings.api_keys) == 0
    assert settings.rate_limit_per_minute == 30


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("RATE_LIMIT_PER_MINUTE", "0"),
        ("RATE_LIMIT_PER_MINUTE", "many"),
        ("ENVIRONMENT", "staging"),
        ("API_KEYS", "key-without-client"),
    ],
    ids=["zero_limit", "non_numeric_limit", "unknown_environment", "malformed_keys"],
)
def test_invalid_production_setting_names_the_variable(variable, value):
    with pytest.raises(ConfigError, match=variable):
        load_settings({variable: value})


def test_settings_repr_never_shows_an_api_key():
    settings = load_settings({"API_KEYS": "ops:repr-secret-0123456789"})

    assert settings.api_keys == {"ops": "repr-secret-0123456789"}
    assert "repr-secret-0123456789" not in repr(settings)
