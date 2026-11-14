"""Pruebas del enrutador: una regla fija elige el modelo de cada pregunta."""

from app.config import load_settings
from app.router import choose_route

# Una pregunta corta por palabra clave, con mayúsculas o sin acento.
KEYWORD_QUESTIONS = [
    "COMPARA Finanzas y Ventas",
    "Resume el área",
    "Explica el puesto",
    "¿Por que se fue?",
    "ANALIZA el equipo",
    "¿Todos están?",
]


def test_every_keyword_sends_the_question_to_the_large_model():
    settings = load_settings({})
    for question in KEYWORD_QUESTIONS:
        route = choose_route(question, settings)
        assert route.model == settings.model_large, question
        assert route.reason.startswith("keyword:"), question


def test_a_question_at_the_char_limit_is_small_and_one_more_is_large():
    settings = load_settings({"ROUTER_MAX_CHARS": "80"})

    at_limit = choose_route("  " + "x" * 80 + "  ", settings)
    over_limit = choose_route("x" * 81, settings)

    assert (at_limit.model, at_limit.reason) == (settings.model_small, "simple")
    assert (over_limit.model, over_limit.reason) == (
        settings.model_large,
        "long_question",
    )


def test_disabled_routing_sends_everything_to_the_large_model():
    settings = load_settings({"ROUTING": "false"})

    route = choose_route("¿Quién es EMP-0004?", settings)

    assert route.model == settings.model_large
    assert route.reason == "routing_disabled"
