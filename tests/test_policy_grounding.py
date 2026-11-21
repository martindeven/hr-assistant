"""Reglas nuevas del reviewer: fundamento de ``policies`` y mensajes internos.

Corrección en producción (reto, parte 1). El reviewer recibe, aparte de la
respuesta completa, la parte que escribió ``policies`` (``policy_answer``) y el
texto de la política que se consultó (``policy_text``; ``None`` si no hubo
consulta).

Un número de esa parte está fundamentado solo si aparece en la política seguido
de la misma unidad (comparando con ``fold``) y si su cláusula comparte al menos
una palabra con contenido con una cláusula de la política que trae ese número
con esa unidad. Así «Te tocan 15 días.» no pasa: el 15 de la política habla de
anticipación, no de los días que tocan.
"""

import pytest

from app.llm import HELP_ANSWER, POLICY_ANSWER_PREFIX
from app.policies import PolicyBook
from app.reviewer import review

DIRECTORY_PART = "EMP-0004 tiene 7 años en la empresa."
GROUNDED_ANSWERS = (
    "Te tocan 12 días hábiles el primer año.",
    "Se piden con 15 días de anticipación.",
    "Te tocan 12 días hábiles.",
    "Te tocan 12 dias habiles.",
)
UNGROUNDED_ANSWERS = (
    "Te tocan 15 días.",
    "Te tocan 12 días.",
    "Te tocan 14 días.",
)
INTERNAL_MESSAGES = (
    "La respuesta debe tener 'answer' o 'tool'.",
    "La respuesta del modelo no es JSON válido.",
    "La herramienta 'delete_everything' no está permitida para este especialista.",
    "La herramienta get_employee necesita el argumento 'employee_id'.",
    'Resultado de la herramienta: {"error": "sin datos"}',
)


class AuditRecorder:
    """Auditor de prueba que guarda cada evento."""

    def __init__(self):
        self.events = []

    def __call__(self, action, outcome, **fields):
        """Guarda la acción, el resultado y los campos."""
        self.events.append((action, outcome, fields))


@pytest.fixture
def book(settings):
    """Las políticas de ``data/policies.json``."""
    return PolicyBook.load(settings.policies_path)


@pytest.fixture
def vacations(book):
    """El texto de la política de vacaciones."""
    return book.search_policy("vacaciones")["text"]


def review_policy(text, policy_text, audit=None):
    """Revisa una respuesta que escribió solo ``policies``."""
    return review(
        text,
        set(),
        [],
        [],
        audit=audit,
        policy_answer=text,
        policy_text=policy_text,
    )


def test_fifteen_days_is_replaced_because_the_policy_uses_it_for_the_notice(
    vacations,
):
    audit = AuditRecorder()

    reviewed = review_policy("Te tocan 15 días.", vacations, audit=audit)

    assert reviewed.answer == POLICY_ANSWER_PREFIX + vacations
    assert reviewed.review == "replaced"
    assert audit.events == [
        ("output_reviewed", "replaced", {"rule": "policy_ungrounded"})
    ]


@pytest.mark.parametrize("text", UNGROUNDED_ANSWERS)
def test_a_policy_number_without_its_context_in_the_policy_is_replaced(
    vacations, text
):
    reviewed = review_policy(text, vacations)

    assert reviewed.answer == POLICY_ANSWER_PREFIX + vacations
    assert reviewed.review == "replaced"


@pytest.mark.parametrize("text", GROUNDED_ANSWERS)
def test_a_policy_number_with_its_unit_and_context_in_the_policy_passes(
    vacations, text
):
    audit = AuditRecorder()

    reviewed = review_policy(text, vacations, audit=audit)

    assert reviewed.answer == text
    assert reviewed.review == "passed"
    assert not audit.events


def test_an_invented_aguinaldo_is_replaced_by_the_aguinaldo_policy(book):
    aguinaldo = book.search_policy("aguinaldo")["text"]

    reviewed = review_policy("Te dan 30 días de aguinaldo.", aguinaldo)

    assert reviewed.answer == POLICY_ANSWER_PREFIX + aguinaldo
    assert reviewed.review == "replaced"


def test_the_fake_model_answer_that_quotes_the_policy_passes(vacations):
    quoted = POLICY_ANSWER_PREFIX + vacations

    reviewed = review_policy(quoted, vacations)

    assert reviewed.answer == quoted
    assert reviewed.review == "passed"


def test_a_policy_answer_without_a_consulted_policy_is_replaced():
    audit = AuditRecorder()

    reviewed = review_policy("Te tocan 15 días.", None, audit=audit)

    assert reviewed.review == "replaced"
    assert "15" not in reviewed.answer
    assert audit.events == [
        ("output_reviewed", "replaced", {"rule": "policy_ungrounded"})
    ]


def test_numbers_of_the_directory_part_do_not_trigger_the_policy_rule(vacations):
    policy_part = "Te tocan 12 días hábiles."
    answer = f"{DIRECTORY_PART}\n{policy_part}"

    reviewed = review(
        answer,
        {"EMP-0004"},
        [],
        [],
        policy_answer=policy_part,
        policy_text=vacations,
    )

    assert reviewed.answer == answer
    assert reviewed.review == "passed"


def test_only_the_policy_part_of_a_mixed_answer_is_replaced(vacations):
    answer = f"{DIRECTORY_PART}\nTe tocan 15 días."

    reviewed = review(
        answer,
        {"EMP-0004"},
        [],
        [],
        policy_answer="Te tocan 15 días.",
        policy_text=vacations,
    )

    assert reviewed.answer == f"{DIRECTORY_PART}\n{POLICY_ANSWER_PREFIX}{vacations}"
    assert reviewed.review == "replaced"


def test_without_a_policy_part_the_numbers_of_the_answer_are_not_checked():
    reviewed = review(DIRECTORY_PART, {"EMP-0004"}, [], [])

    assert reviewed.answer == DIRECTORY_PART
    assert reviewed.review == "passed"


@pytest.mark.parametrize("message", INTERNAL_MESSAGES)
def test_an_internal_message_of_the_agent_is_replaced_by_the_help(message):
    audit = AuditRecorder()

    reviewed = review(f"Lo siento. {message}", set(), [], [], audit=audit)

    assert reviewed.answer == HELP_ANSWER
    assert reviewed.review == "replaced"
    assert audit.events == [
        ("output_reviewed", "replaced", {"rule": "internal_message"})
    ]
