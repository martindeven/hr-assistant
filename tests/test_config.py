"""Pruebas de ``app.config``: valores por omisión y valores inválidos."""

from pathlib import Path

import pytest

from app.config import ConfigError, load_settings

PRODUCT_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_used_when_the_environment_is_empty():
    settings = load_settings({})

    assert settings.model_provider == "fake"
    assert settings.agent_max_steps == 3
    assert settings.max_question_chars == 2000
    expected_path = PRODUCT_ROOT / "data" / "employees.json"
    assert Path(settings.employees_path).resolve() == expected_path.resolve()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("AGENT_MAX_STEPS", "0"),
        ("AGENT_MAX_STEPS", "three"),
        ("MAX_QUESTION_CHARS", "-5"),
        ("MODEL_PROVIDER", "openai"),
        ("EMPLOYEES_PATH", "data/does-not-exist.json"),
    ],
    ids=[
        "zero_steps",
        "non_numeric_steps",
        "negative_question_size",
        "unknown_provider",
        "missing_data_file",
    ],
)
def test_invalid_setting_stops_startup_with_a_clear_message(variable, value):
    # El mensaje debe nombrar la variable que falla.
    with pytest.raises(ConfigError, match=variable):
        load_settings({variable: value})


def test_observability_defaults_are_safe():
    settings = load_settings({})

    assert settings.service_name == "hr-assistant"
    assert settings.slo_p95_ms == 2000
    assert settings.tracing_enabled is False
    assert settings.hide_inputs is True
    assert settings.hide_outputs is True


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SLO_P95_MS", "0"),
        ("SLO_P95_MS", "fast"),
        ("SERVICE_NAME", "   "),
        ("LANGSMITH_TRACING", "yes"),
        ("HIDE_INPUTS", "1"),
        ("HIDE_OUTPUTS", "maybe"),
    ],
    ids=[
        "zero_slo",
        "non_numeric_slo",
        "blank_service_name",
        "tracing_not_boolean",
        "hide_inputs_not_boolean",
        "hide_outputs_not_boolean",
    ],
)
def test_invalid_observability_setting_names_the_variable(variable, value):
    with pytest.raises(ConfigError, match=variable):
        load_settings({variable: value})


def test_tracing_needs_an_api_key_that_is_never_stored():
    fake_key = "lsv2-test-key-not-real"

    without_key = load_settings({"LANGSMITH_TRACING": "true"})
    with_key = load_settings(
        {"LANGSMITH_TRACING": "TRUE", "LANGSMITH_API_KEY": fake_key}
    )

    assert without_key.tracing_enabled is False
    assert with_key.tracing_enabled is True
    assert fake_key not in repr(with_key)
