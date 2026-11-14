"""Pruebas de ``app.agent.Agent`` con un modelo de respuestas guionadas."""

from app.agent import Agent

TOOL_RESULT_PREFIX = "Resultado de la herramienta: "


def ask(text):
    """Arma la conversación con una sola pregunta del usuario."""
    return [{"role": "user", "content": text}]


def test_tools_run_in_order_and_tokens_add_up(directory, scripted_model):
    model = scripted_model(
        [
            '{"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}',
            '{"tool": "search_by_department", "args": {"department": "Ventas"}}',
            '{"answer": "Listo"}',
        ]
    )
    agent = Agent(model=model, directory=directory, max_steps=3)

    result = agent.run(ask("¿Quién es EMP-0004 y quién está en Ventas?"))

    assert result.answer == "Listo"
    assert result.steps == 3
    assert result.tools_used == ["get_employee", "search_by_department"]
    assert result.input_tokens == 30
    assert result.output_tokens == 6
    # El resultado de la herramienta vuelve al modelo sin acentos escapados.
    tool_result = model.calls[1][-1]["content"]
    assert tool_result.startswith(TOOL_RESULT_PREFIX)
    assert "Jorge Díaz" in tool_result


def test_protocol_errors_are_returned_to_the_model_and_the_loop_continues(
    directory, scripted_model
):
    model = scripted_model(
        [
            "this is not JSON",
            '{"tool": "delete_employee", "args": {"employee_id": "EMP-0004"}}',
            '{"tool": "get_employee", "args": {}}',
            '{"answer": "Listo"}',
        ]
    )
    agent = Agent(model=model, directory=directory, max_steps=5)

    result = agent.run(ask("¿Quién es EMP-0004?"))

    assert result.answer == "Listo"
    assert result.steps == 4
    error_results = [
        message
        for message in model.calls[-1]
        if message["role"] == "user"
        and message["content"].startswith(TOOL_RESULT_PREFIX)
    ]
    assert len(error_results) == 3


def test_stopped_is_true_only_when_the_step_cap_is_reached(
    directory, scripted_model
):
    tool_call = '{"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}'
    capped = Agent(
        model=scripted_model([tool_call]), directory=directory, max_steps=1
    )
    answered = Agent(
        model=scripted_model(['{"answer": "Listo"}']),
        directory=directory,
        max_steps=1,
    )

    assert capped.run(ask("¿Quién es EMP-0004?")).stopped is True
    assert answered.run(ask("Hola")).stopped is False


def test_every_requested_tool_is_reported_before_running_even_if_unknown(
    directory, scripted_model
):
    model = scripted_model(
        [
            '{"tool": "delete_employee", "args": {"employee_id": "EMP-0004"}}',
            '{"tool": "get_employee", "args": {"employee_id": "EMP-0004"}}',
            '{"answer": "Listo"}',
        ]
    )
    agent = Agent(model=model, directory=directory, max_steps=3)
    reported = []

    result = agent.run(
        ask("¿Quién es EMP-0004?"),
        on_tool_call=lambda name, args: reported.append((name, args)),
    )

    assert reported == [
        ("delete_employee", {"employee_id": "EMP-0004"}),
        ("get_employee", {"employee_id": "EMP-0004"}),
    ]
    # Solo la herramienta que existe cuenta como usada.
    assert result.tools_used == ["get_employee"]
