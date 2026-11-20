"""Pruebas unitarias del sistema multiagente (reto, parte 1, puntos 1 y 3.3).

Los especialistas se prueban con modelos guionados: el guion decide qué pide
el modelo y la prueba revisa qué hizo el producto con eso.
"""

import json

from app.llm import HELP_ANSWER, SYSTEM_PROMPTS, TOOL_RESULT_PREFIX, FakeModel
from app.policies import PolicyBook
from app.supervisor import plan, restrict

UNVERIFIED_ANSWER = "No pude verificar la respuesta con el directorio."
NO_TOPIC_QUESTION = "Hola, ¿me ayudas?"


def tool(name, **args):
    """Una respuesta del modelo que pide una herramienta."""
    return json.dumps({"tool": name, "args": args})


def answer(text):
    """Una respuesta final del modelo."""
    return json.dumps({"answer": text}, ensure_ascii=False)


def audits(observed, action):
    """Los eventos ``audit`` de una acción."""
    return [
        event
        for event in observed.events()
        if event["event"] == "audit" and event.get("action") == action
    ]


class SpyDirectory:
    """Directorio que anota cada lectura y delega en el real.

    :param directory: Directorio real
    :type directory: app.directory.EmployeeDirectory
    """

    def __init__(self, directory):
        self._directory = directory
        self.reads = []

    def departments(self):
        """Los departamentos del directorio real."""
        return self._directory.departments()

    def get_employee(self, employee_id):
        """Anota la lectura y busca al empleado."""
        self.reads.append(("get_employee", employee_id))
        return self._directory.get_employee(employee_id)

    def search_by_department(self, department):
        """Anota la lectura y busca el departamento."""
        self.reads.append(("search_by_department", department))
        return self._directory.search_by_department(department)


def test_policies_are_found_by_topic_or_keyword_without_accents(settings):
    book = PolicyBook.load(settings.policies_path)

    assert "12 días" in book.search_policy("vacaciones")["text"]
    assert book.search_policy("DÍAS LIBRES")["topic"] == "vacaciones"
    assert "error" in book.search_policy("bonos de productividad")
    assert book.find_keyword("Necesito un permiso médico") == "incapacidad"
    assert book.find_keyword("Necesito un permiso") == "permisos"
    assert book.find_keyword("¿Quién es EMP-0004?") is None


def test_the_supervisor_picks_specialists_and_keeps_only_allowed_ones(
    settings, directory
):
    book = PolicyBook.load(settings.policies_path)
    departments = directory.departments()

    assert plan("¿quién es emp-0004?", departments, book) == ["directory"]
    assert plan("¿Quiénes están en TECNOLOGIA?", departments, book) == ["directory"]
    assert plan("¿Cuántas VACACIONES tengo?", departments, book) == ["policies"]
    assert plan("¿Inventas las vacaciones?", departments, book) == ["policies"]
    assert plan("¿EMP-0004 tiene vacaciones?", departments, book) == [
        "directory",
        "policies",
    ]
    assert plan(NO_TOPIC_QUESTION, departments, book) == ["directory"]
    assert restrict(["directory", "policies"], {"policies"}) == ["policies"]
    assert restrict(["directory"], {"policies", "metrics"}) == []


def test_the_fake_model_answers_policies_only_with_search_policy(directory):
    model = FakeModel(directory.departments())
    system = {"role": "system", "content": SYSTEM_PROMPTS["policies"]}
    question = {"role": "user", "content": "¿EMP-0004 tiene vacaciones?"}

    request = json.loads(model.complete([system, question]).content)
    result = {"role": "user", "content": TOOL_RESULT_PREFIX + '{"text": "12 días"}'}
    reply = json.loads(model.complete([system, question, result]).content)

    assert request == {"tool": "search_policy", "args": {"topic": "vacaciones"}}
    assert "12 días" in reply["answer"]


def test_a_tool_of_another_specialist_or_an_unknown_tool_is_denied(
    make_observed_client, scripted_model
):
    model = scripted_model(
        [
            tool("get_employee", employee_id="EMP-0004"),
            tool("delete_everything", target="all"),
            answer("No encontré la política."),
        ]
    )
    observed = make_observed_client(model=model)

    body = observed.http.post(
        "/ask", json={"question": "¿Cuántos días de vacaciones tengo?"}
    ).json()

    assert body["agents"] == ["guard", "supervisor", "policies", "reviewer"]
    assert body["tools_used"] == []
    denied = audits(observed, "tool_denied")
    assert [event["tool"] for event in denied] == ["get_employee", "delete_everything"]
    assert "error" in model.calls[1][-1]["content"]


def test_invalid_tool_arguments_return_an_error_without_reading_data(
    make_observed_client, scripted_model, directory
):
    spy = SpyDirectory(directory)
    model = scripted_model(
        [
            tool("get_employee", employee_id="emp-0001"),
            tool("search_by_department", department="x" * 61),
            answer("No pude encontrar a nadie."),
        ]
    )
    observed = make_observed_client(model=model, directory=spy)

    observed.http.post("/ask", json={"question": NO_TOPIC_QUESTION})

    assert not spy.reads
    assert "error" in model.calls[1][-1]["content"]
    assert "error" in model.calls[2][-1]["content"]


def test_an_unverified_employee_id_in_the_answer_is_replaced(
    make_observed_client, scripted_model
):
    model = scripted_model([answer("El director general es EMP-0099.")])
    observed = make_observed_client(model=model)

    body = observed.http.post(
        "/ask", json={"question": NO_TOPIC_QUESTION}
    ).json()

    assert body["answer"] == UNVERIFIED_ANSWER
    assert body["guardrails"]["review"] == "replaced"
    assert audits(observed, "output_reviewed")


def test_a_leaked_system_prompt_is_replaced_and_output_pii_is_redacted(
    make_observed_client, scripted_model
):
    leak = SYSTEM_PROMPTS["directory"][:80]
    leaked = make_observed_client(model=scripted_model([answer(leak)]))
    with_pii = make_observed_client(
        model=scripted_model([answer("Escribe a ana@example.com")])
    )

    leak_body = leaked.http.post("/ask", json={"question": NO_TOPIC_QUESTION}).json()
    pii_body = with_pii.http.post("/ask", json={"question": NO_TOPIC_QUESTION}).json()

    assert leak[:40] not in leak_body["answer"]
    assert leak_body["guardrails"]["review"] == "replaced"
    assert pii_body["answer"] == "Escribe a [EMAIL]"
    assert pii_body["guardrails"]["pii_output"] == ["email"]
    assert pii_body["guardrails"]["review"] == "redacted"
    assert "ana@example.com" not in with_pii.log_stream.getvalue()


def test_empty_answers_get_the_help_and_long_answers_are_cut(
    make_observed_client, scripted_model
):
    empty = make_observed_client(model=scripted_model([answer("")]))
    long = make_observed_client(
        model=scripted_model([answer("a" * 80)]), env={"MAX_ANSWER_CHARS": "50"}
    )

    empty_body = empty.http.post("/ask", json={"question": NO_TOPIC_QUESTION}).json()
    long_body = long.http.post("/ask", json={"question": NO_TOPIC_QUESTION}).json()

    assert empty_body["answer"] == HELP_ANSWER
    assert empty_body["guardrails"]["review"] == "replaced"
    assert len(long_body["answer"]) == 50


def test_redacted_input_never_reaches_the_model(make_observed_client, scripted_model):
    model = scripted_model([answer("Listo.")])
    observed = make_observed_client(model=model)
    history = [{"role": "user", "content": "Mi correo es ana@example.com"}]
    question = "Mi teléfono es 55 1234 5678, ¿me ayudas?"

    body = observed.http.post(
        "/ask", json={"question": question, "history": history}
    ).json()

    sent = json.dumps(model.calls, ensure_ascii=False)
    assert "ana@example.com" not in sent and "1234 5678" not in sent
    assert "[EMAIL]" in sent and "[PHONE]" in sent
    assert set(body["guardrails"]["pii_input"]) == {"email", "phone"}
