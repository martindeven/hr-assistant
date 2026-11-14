"""Pruebas de ``app.llm.FakeModel``: prioridad de reglas y conteo de tokens."""

import json

from app.llm import FakeModel

HELP_ANSWER = "Pregúntame por un empleado (EMP-0001) o un departamento."


def test_tool_result_is_answered_even_if_it_contains_an_employee_id():
    model = FakeModel(["Finanzas"])
    result = '{"id": "EMP-0005", "name": "Sofía Gómez"}'
    messages = [
        {"role": "user", "content": "¿Quién es EMP-0005?"},
        {"role": "user", "content": f"Resultado de la herramienta: {result}"},
    ]

    completion = model.complete(messages)

    assert json.loads(completion.content) == {
        "answer": f"Según el directorio: {result}"
    }


def test_tokens_are_counted_as_words_of_the_messages_and_the_reply():
    model = FakeModel(["Finanzas"])
    messages = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Hola, dime"},
        {"role": "user", "content": "¿Qué tal estás hoy?"},
    ]

    completion = model.complete(messages)

    assert json.loads(completion.content) == {"answer": HELP_ANSWER}
    assert completion.input_tokens == 1 + 2 + 4
    assert completion.output_tokens == len(completion.content.split())
