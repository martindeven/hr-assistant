"""Agente ``reviewer``: valida la respuesta final antes de devolverla.

Es el único lugar donde se revisa la salida. No usa modelo: solo reglas, en este
orden (punto 3.3 de la especificación y plan):

1. Respuesta vacía: se reemplaza por la ayuda fija (``replaced``).
2. Filtración del prompt: si contiene el inicio de un prompt de sistema, se
   reemplaza por un mensaje seguro (``replaced``).
3. Fundamento: un id ``EMP-####`` que no viene de la pregunta ni de un resultado
   de herramienta hace que se reemplace (``replaced``); los textos fijos del
   producto quedan exentos.
4. Datos personales: se tapan (``redacted``).
5. Largo: se recorta a ``MAX_ANSWER_CHARS`` (no cambia ``review``).

Cada ``replaced`` o ``redacted`` audita ``output_reviewed`` con el id de la
regla; nunca el texto.
"""

import re
from dataclasses import dataclass, field

from app import pii
from app.llm import HELP_ANSWER
from app.text import fold

REVIEWER_AGENT = "reviewer"
REVIEW_PASSED = "passed"
REVIEW_REDACTED = "redacted"
REVIEW_REPLACED = "replaced"
PROMPT_HEAD_CHARS = 40
SAFE_ANSWER = "No puedo compartir esa información."
UNVERIFIED_ANSWER = "No pude verificar la respuesta con el directorio."
ANSWER_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])EMP-\d{4}(?!\d)", re.IGNORECASE)
OUTPUT_WHERE = "output"


@dataclass(frozen=True)
class ReviewResult:
    """Respuesta revisada.

    :param answer: Respuesta que se devuelve a la persona
    :type answer: str
    :param review: ``passed``, ``redacted`` o ``replaced``
    :type review: str
    :param pii_types: Tipos de dato personal tapados en la salida
    :type pii_types: list[str]
    """

    answer: str
    review: str = REVIEW_PASSED
    pii_types: list = field(default_factory=list)


def _noop_audit(action, outcome, **fields):
    """Auditor que no hace nada, para usar el reviewer sin log."""
    del action, outcome, fields


def _leaks_prompt(answer, prompts):
    """Indica si la respuesta trae el inicio de algún prompt de sistema.

    :param answer: Respuesta a revisar
    :type answer: str
    :param prompts: Prompts de sistema
    :type prompts: collections.abc.Iterable[str]
    :return: ``True`` si aparece el inicio de alguno (sin mayúsculas ni acentos)
    :rtype: bool
    """
    folded = fold(answer)
    for prompt in prompts:
        head = fold(str(prompt or "")[:PROMPT_HEAD_CHARS])
        if head and head in folded:
            return True
    return False


def _is_ungrounded(answer, evidence_ids, exempt_texts):
    """Indica si la respuesta menciona un ``EMP-####`` sin evidencia.

    :param answer: Respuesta a revisar
    :type answer: str
    :param evidence_ids: Ids de la pregunta y de los resultados de herramienta
    :type evidence_ids: set[str]
    :param exempt_texts: Textos fijos del producto, que no cuentan
    :type exempt_texts: collections.abc.Iterable[str]
    :return: ``True`` si hay algún id que no viene de la evidencia
    :rtype: bool
    """
    remaining = answer
    for text in exempt_texts:
        if text:
            remaining = remaining.replace(text, "")
    known = {str(item).upper() for item in evidence_ids}
    return any(
        found.upper() not in known for found in ANSWER_ID_PATTERN.findall(remaining)
    )


def _replacement(answer, evidence_ids, prompts, exempt_texts):
    """Busca la primera regla que obliga a reemplazar la respuesta.

    :param answer: Respuesta a revisar
    :type answer: str
    :param evidence_ids: Ids con evidencia
    :type evidence_ids: set[str]
    :param prompts: Prompts de sistema
    :type prompts: collections.abc.Iterable[str]
    :param exempt_texts: Textos fijos del producto
    :type exempt_texts: collections.abc.Iterable[str]
    :return: El id de la regla y el texto de reemplazo, o ``None``
    :rtype: tuple[str, str] or None
    """
    if not answer.strip():
        return "empty", HELP_ANSWER
    if _leaks_prompt(answer, prompts):
        return "prompt_leak", SAFE_ANSWER
    if _is_ungrounded(answer, evidence_ids, exempt_texts):
        return "ungrounded", UNVERIFIED_ANSWER
    return None


def _cut(text, max_chars):
    """Recorta un texto al largo máximo, si hay uno.

    :param text: Texto a recortar
    :type text: str
    :param max_chars: Largo máximo, o ``None``
    :type max_chars: int or None
    :return: El texto recortado
    :rtype: str
    """
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars]


# La firma es la interfaz acordada en el plan: cada argumento es una regla
# distinta del reviewer, así que pasa del límite de argumentos de pylint.
def review(  # pylint: disable=too-many-arguments
    answer,
    evidence_ids,
    prompts,
    exempt_texts,
    *,
    max_chars=None,
    audit=None,
):
    """Revisa la respuesta final de los especialistas.

    :param answer: Respuesta juntada de los especialistas
    :type answer: str
    :param evidence_ids: Ids ``EMP-####`` que vienen de la pregunta o de un
        resultado de herramienta
    :type evidence_ids: set[str]
    :param prompts: Prompts de sistema que no deben filtrarse
    :type prompts: collections.abc.Iterable[str]
    :param exempt_texts: Textos fijos del producto (la ayuda), exentos del
        fundamento
    :type exempt_texts: collections.abc.Iterable[str]
    :param max_chars: Largo máximo de la respuesta (``MAX_ANSWER_CHARS``)
    :type max_chars: int or None
    :param audit: Función ``(action, outcome, **campos)`` que audita los
        reemplazos (``output_reviewed``) y la PII (``pii_detected``); opcional
    :type audit: collections.abc.Callable or None
    :return: La respuesta revisada, el resultado y los tipos de PII
    :rtype: ReviewResult
    """
    audit = audit or _noop_audit
    text = str(answer or "")
    replacement = _replacement(text, evidence_ids, list(prompts), list(exempt_texts))
    if replacement is not None:
        rule, safe_text = replacement
        audit("output_reviewed", REVIEW_REPLACED, rule=rule)
        return ReviewResult(
            answer=_cut(safe_text, max_chars), review=REVIEW_REPLACED, pii_types=[]
        )
    redacted, found = pii.redact(text)
    types = pii.ordered_types(found)
    outcome = REVIEW_PASSED
    if types:
        outcome = REVIEW_REDACTED
        audit(
            "pii_detected",
            REVIEW_REDACTED,
            where=OUTPUT_WHERE,
            types=types,
            count=sum(found.values()),
        )
        audit("output_reviewed", REVIEW_REDACTED, rule="pii")
    return ReviewResult(
        answer=_cut(redacted, max_chars), review=outcome, pii_types=types
    )
