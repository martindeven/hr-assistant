"""Pasos de los escenarios de ``features/policy_grounding.feature``.

Corrección en producción (reto, parte 1, especialista ``policies``): con el
modelo real, la pregunta de vacaciones respondía «15 días» sin consultar la
política. Los modelos guionados imitan a ese modelo: contestan sin pedir
herramientas, y la prueba revisa que el producto consulte la política antes y
que el reviewer reemplace los números que no vienen de ella.
"""

import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from app.llm import HELP_ANSWER, TOOL_RESULT_PREFIX, FakeModel

scenarios("features/policy_grounding.feature")


def _answer(text):
    """Una respuesta final del modelo, en el protocolo JSON."""
    return json.dumps({"answer": text}, ensure_ascii=False)


def _tool(name, **args):
    """Una respuesta del modelo que pide una herramienta."""
    return json.dumps({"tool": name, "args": args})


def _split(text):
    """Convierte ``"a, b"`` en ``["a", "b"]``."""
    return [item.strip() for item in text.split(",")]


def _audits(observed, action):
    """Los eventos ``audit`` de una acción, en orden."""
    return [
        event
        for event in observed.events()
        if event["event"] == "audit" and event.get("action") == action
    ]


@pytest.fixture
def used_models():
    """Guarda el modelo de cada escenario para revisar sus llamadas."""
    return {}


@given(
    parsers.parse(
        'the HR assistant is running with a model that answers "{reply}" '
        "without asking for a tool"
    ),
    target_fixture="observed",
)
def running_with_a_model_that_never_asks(
    make_observed_client, scripted_model, used_models, reply
):
    """El modelo contesta de una vez, sin pedir herramientas (como el real)."""
    model = scripted_model([_answer(reply)])
    used_models["model"] = model
    return make_observed_client(model=model)


@given(
    parsers.parse(
        'the HR assistant is running with a model whose directory part says '
        '"{directory_part}" and policies part says "{policies_part}"'
    ),
    target_fixture="observed",
)
def running_with_a_mixed_model(
    make_observed_client, scripted_model, used_models, directory_part, policies_part
):
    """``directory`` pide al empleado y responde; ``policies`` responde de una vez."""
    model = scripted_model(
        [
            _tool("get_employee", employee_id="EMP-0004"),
            _answer(directory_part),
            _answer(policies_part),
        ]
    )
    used_models["model"] = model
    return make_observed_client(model=model)


@given(
    "the HR assistant is running with the fake model for policies",
    target_fixture="observed",
)
def running_with_the_fake_model(make_observed_client, directory):
    """La API con el modelo simulado de siempre."""
    return make_observed_client(model=FakeModel(directory.departments()))


@when(parsers.parse('I ask "{question}"'), target_fixture="response")
def ask(observed, question):
    """Manda una pregunta sin historial."""
    return observed.http.post("/ask", json={"question": question})


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then(parsers.parse('the answer contains "{text}"'))
def answer_contains(response, text):
    """La respuesta trae el texto indicado."""
    assert text in response.json()["answer"]


@then(parsers.parse('the answer does not contain "{text}"'))
def answer_does_not_contain(response, text):
    """La respuesta no trae el texto indicado."""
    assert text not in response.json()["answer"]


@then("the answer is the help")
def answer_is_the_help(response):
    """La respuesta es exactamente la ayuda fija."""
    assert response.json()["answer"] == HELP_ANSWER


@then(parsers.parse('the tools used are "{tools}"'))
def tools_are(response, tools):
    """Las herramientas ejecutadas, en orden y sin repetir la consulta."""
    assert response.json()["tools_used"] == _split(tools)


@then(parsers.parse('the agents are "{agents}"'))
def agents_are(response, agents):
    """Los agentes que corrieron, en orden."""
    assert response.json()["agents"] == _split(agents)


@then(parsers.parse('the review is "{review}"'))
def review_is(response, review):
    """El resultado del reviewer."""
    assert response.json()["guardrails"]["review"] == review


@then(parsers.parse("the steps are {steps:d}"))
def steps_are(response, steps):
    """Las llamadas al modelo de toda la pregunta."""
    assert response.json()["steps"] == steps


@then(
    parsers.parse(
        'the log has one tool_call audit for "{tool}" with the topic "{topic}"'
    )
)
def one_tool_call_audit(observed, tool, topic):
    """La consulta previa se audita una sola vez, con su tema."""
    calls = [
        event for event in _audits(observed, "tool_call") if event["tool"] == tool
    ]
    assert len(calls) == 1
    assert calls[0]["args"] == {"topic": topic}
    assert {"client", "request_id", "outcome"} <= set(calls[0])


@then(parsers.parse('the log has an output_reviewed audit with outcome "{outcome}"'))
def output_reviewed_audit(observed, outcome):
    """El reemplazo del reviewer queda auditado, sin el texto."""
    reviewed = _audits(observed, "output_reviewed")
    assert [event["outcome"] for event in reviewed] == [outcome]
    assert "answer" not in reviewed[0] and "text" not in reviewed[0]


@then(
    parsers.parse(
        'the model was called once and had already received the policy text "{text}"'
    )
)
def model_received_the_policy(used_models, text):
    """El modelo solo redacta: la política ya venía como resultado de herramienta."""
    calls = used_models["model"].calls
    assert len(calls) == 1
    results = [
        message["content"]
        for message in calls[0]
        if str(message.get("content", "")).startswith(TOOL_RESULT_PREFIX)
    ]
    assert results and text in results[0]
