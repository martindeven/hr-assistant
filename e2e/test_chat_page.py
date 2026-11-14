"""Pruebas de punta a punta de la página del HR Assistant (spec/03 y spec/04).

Corren en el navegador contra el producto vivo con el modelo simulado. La URL
base la pone ``verification e2e`` con ``--base-url``, y la clave del cliente
llega en la variable ``E2E_API_KEY``: nunca se escribe en la prueba.
"""

import os
import re

from playwright.sync_api import Page, expect

UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
REQUEST_ID_LINE = re.compile(r"Id de la petición:\s*" + UUID_PATTERN)
FIRST_QUESTION = "¿En qué departamento trabaja EMP-0001?"
SECOND_QUESTION = "¿Y quién es el jefe de EMP-0001?"
WRONG_KEY_SUFFIX = "-not-a-valid-key"


def _api_key() -> str:
    """Devuelve la clave del cliente que pone la verificación.

    :return: Clave leída de ``E2E_API_KEY``
    :rtype: str
    """
    return os.environ["E2E_API_KEY"]


def _open_page_with_key(page: Page, api_key: str) -> None:
    """Abre la página y escribe la clave en su campo.

    :param page: Página del navegador
    :type page: playwright.sync_api.Page
    :param api_key: Clave que se escribe en el campo ``api-key``
    :type api_key: str
    """
    page.goto("/")
    page.get_by_test_id("api-key").fill(api_key)


def _ask(page: Page, question: str) -> None:
    """Escribe una pregunta en la caja y la envía con el botón.

    :param page: Página del navegador
    :type page: playwright.sync_api.Page
    :param question: Texto de la pregunta
    :type question: str
    """
    page.get_by_test_id("question").fill(question)
    page.get_by_test_id("send").click()


def test_asking_a_question_shows_the_answer_and_its_request_id(page: Page):
    """Con la clave correcta se ven la pregunta, la respuesta y su request_id."""
    _open_page_with_key(page, _api_key())

    _ask(page, FIRST_QUESTION)

    answer = page.get_by_test_id("answer")
    expect(answer).to_contain_text(FIRST_QUESTION)
    expect(answer).to_contain_text(re.compile(r"Asistente:\s*\S+"))
    expect(answer).to_contain_text(REQUEST_ID_LINE)


def test_an_empty_question_shows_an_error_message_in_spanish(page: Page):
    """Enviar la caja vacía muestra un error en español y no llama al asistente."""
    _open_page_with_key(page, _api_key())

    page.get_by_test_id("send").click()

    answer = page.get_by_test_id("answer")
    expect(answer).to_contain_text("Error: escribe una pregunta.")
    expect(answer).not_to_contain_text("Asistente:")


def test_a_blank_question_shows_the_same_spanish_error(page: Page):
    """Una pregunta solo con espacios también cuenta como vacía."""
    _open_page_with_key(page, _api_key())

    _ask(page, "   ")

    answer = page.get_by_test_id("answer")
    expect(answer).to_contain_text("Error: escribe una pregunta.")
    expect(answer).not_to_contain_text("Asistente:")


def test_a_second_question_keeps_the_previous_conversation_on_screen(page: Page):
    """La segunda respuesta se agrega debajo de la primera, sin borrarla."""
    _open_page_with_key(page, _api_key())
    answer = page.get_by_test_id("answer")

    _ask(page, FIRST_QUESTION)
    expect(answer).to_contain_text(REQUEST_ID_LINE)
    expect(page.get_by_test_id("send")).to_be_enabled()

    _ask(page, SECOND_QUESTION)

    two_turns_in_order = re.compile(
        re.escape(FIRST_QUESTION)
        + r".*Asistente:.*Id de la petición:\s*"
        + UUID_PATTERN
        + r".*"
        + re.escape(SECOND_QUESTION)
        + r".*Asistente:.*Id de la petición:\s*"
        + UUID_PATTERN,
        re.DOTALL,
    )
    expect(answer).to_contain_text(two_turns_in_order)


def test_a_wrong_api_key_shows_invalid_key_and_no_answer(page: Page):
    """Una clave equivocada muestra «Clave inválida» y ninguna respuesta."""
    _open_page_with_key(page, _api_key() + WRONG_KEY_SUFFIX)

    _ask(page, FIRST_QUESTION)

    answer = page.get_by_test_id("answer")
    expect(answer).to_contain_text("Clave inválida")
    expect(answer).not_to_contain_text("Asistente:")


def test_the_api_key_is_hidden_and_kept_only_for_the_session(page: Page):
    """El campo oculta la clave y esta se guarda en sessionStorage, no en localStorage."""
    api_key = _api_key()
    _open_page_with_key(page, api_key)
    expect(page.get_by_test_id("api-key")).to_have_attribute("type", "password")

    _ask(page, FIRST_QUESTION)
    expect(page.get_by_test_id("answer")).to_contain_text(REQUEST_ID_LINE)

    session_values = page.evaluate("() => Object.values(window.sessionStorage)")
    local_values = page.evaluate("() => Object.values(window.localStorage)")
    assert api_key in session_values
    assert api_key not in local_values
    assert api_key not in page.content()
