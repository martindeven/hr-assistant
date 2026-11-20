"""Agente ``reviewer``: valida la respuesta final antes de devolverla.

Es el único lugar donde se revisa la salida. No usa modelo: solo reglas, en este
orden (punto 3.3 de la especificación y plan):

1. Respuesta vacía: se reemplaza por la ayuda fija (``replaced``).
2. Filtración del prompt: si contiene el inicio de un prompt de sistema, se
   reemplaza por un mensaje seguro (``replaced``).
3. Mensajes internos: si trae un mensaje interno del agente (errores del
   protocolo o el prefijo de resultado de herramienta), se reemplaza por la
   ayuda fija (``replaced``, regla ``internal_message``).
4. Fundamento: un id ``EMP-####`` que no viene de la pregunta ni de un resultado
   de herramienta hace que se reemplace (``replaced``); los textos fijos del
   producto quedan exentos.
5. Fundamento de ``policies``: cada número de la parte que escribió
   ``policies`` debe venir de la política consultada, con su misma unidad y su
   mismo contexto; si no, solo esa parte se cambia por la cita de la política
   (``replaced``, regla ``policy_ungrounded``).
6. Datos personales: se tapan (``redacted``).
7. Largo: se recorta a ``MAX_ANSWER_CHARS`` (no cambia ``review``).

Cada ``replaced`` o ``redacted`` audita ``output_reviewed`` con el id de la
regla; nunca el texto.
"""

import re
from dataclasses import dataclass, field

from app import pii
from app.llm import (
    HELP_ANSWER,
    INTERNAL_MESSAGE_TEMPLATES,
    NO_POLICY_ANSWER,
    POLICY_ANSWER_PREFIX,
)
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
TEMPLATE_FIELD_PATTERN = re.compile(r"\{[^{}]*\}")
CLAUSE_SEPARATOR_PATTERN = re.compile(r"[,;:.\n]")
TOKEN_PATTERN = re.compile(r"\d+|[^\W\d_]+|%")
# Palabras que no cuentan como contexto al comparar una cláusula de la
# respuesta con una de la política (ya pasadas por ``fold``).
POLICY_STOPWORDS = frozenset(
    {
        # Artículos y contracciones.
        "el", "la", "lo", "los", "las", "un", "una", "unos", "unas", "al", "del",
        # Preposiciones y conjunciones.
        "a", "ante", "bajo", "con", "contra", "de", "desde", "durante", "en",
        "entre", "hacia", "hasta", "para", "por", "segun", "sin", "sobre", "tras",
        "y", "e", "o", "u", "ni", "pero", "que", "si", "como", "cuando",
        # Pronombres y posesivos.
        "yo", "tu", "usted", "ustedes", "ella", "ellos", "ellas", "nosotros",
        "te", "me", "se", "le", "les", "nos", "os", "mi", "mis", "tus", "su",
        "sus", "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
        "eso", "esto",
        # Verbos y palabras de relleno de una respuesta.
        "es", "son", "ser", "estan", "hay", "tocan", "toca", "corresponden",
        "corresponde", "tienes", "tiene", "tengo", "tienen", "dan", "da", "dar",
        "recibes", "recibe", "puedes", "puede", "cuantos", "cuantas", "total",
        "muy", "mas", "menos", "no", "ya", "solo", "politica",
    }
)


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


def _template_pattern(template):
    """Convierte un mensaje interno con campos ``{...}`` en un patrón.

    El texto fijo se compara con :func:`fold`; cada campo acepta cualquier
    texto.

    :param template: Mensaje o plantilla de ``app.llm``
    :type template: str
    :return: El patrón que lo reconoce dentro de un texto normalizado
    :rtype: re.Pattern
    """
    parts = TEMPLATE_FIELD_PATTERN.split(fold(template))
    return re.compile(".+?".join(re.escape(part) for part in parts))


INTERNAL_MESSAGE_PATTERNS = tuple(
    _template_pattern(template) for template in INTERNAL_MESSAGE_TEMPLATES
)


def _has_internal_message(answer):
    """Indica si la respuesta trae un mensaje interno del agente.

    :param answer: Respuesta a revisar
    :type answer: str
    :return: ``True`` si aparece alguno (sin mayúsculas ni acentos)
    :rtype: bool
    """
    folded = fold(answer)
    return any(pattern.search(folded) for pattern in INTERNAL_MESSAGE_PATTERNS)


def _clauses(text):
    """Parte un texto en cláusulas y cada cláusula en palabras normalizadas.

    :param text: Texto de la respuesta o de la política
    :type text: str
    :return: Las palabras de cada cláusula no vacía
    :rtype: list[list[str]]
    """
    clauses = []
    for clause in CLAUSE_SEPARATOR_PATTERN.split(text):
        tokens = TOKEN_PATTERN.findall(fold(clause))
        if tokens:
            clauses.append(tokens)
    return clauses


def _numbers(tokens):
    """Saca cada número de una cláusula con la palabra que lo sigue.

    :param tokens: Palabras de la cláusula
    :type tokens: list[str]
    :return: Pares ``(número, unidad)``; la unidad es ``None`` si no hay
        palabra después
    :rtype: list[tuple[str, str or None]]
    """
    found = []
    for index, token in enumerate(tokens):
        if not token.isdigit():
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        unit = following if following and not following.isdigit() else None
        found.append((token, unit))
    return found


def _has_number(tokens, number, unit):
    """Indica si una cláusula trae el número seguido de la misma unidad.

    :param tokens: Palabras de la cláusula
    :type tokens: list[str]
    :param number: Número buscado
    :type number: str
    :param unit: Palabra que lo sigue, o ``None`` si basta el número
    :type unit: str or None
    :return: ``True`` si aparece
    :rtype: bool
    """
    if unit is None:
        return number in tokens
    return (number, unit) in _numbers(tokens)


def _content_words(tokens, unit):
    """Palabras con contenido de una cláusula: sin números, unidad ni vacías.

    :param tokens: Palabras de la cláusula
    :type tokens: list[str]
    :param unit: Unidad del número que se revisa, o ``None``
    :type unit: str or None
    :return: Las palabras con contenido
    :rtype: set[str]
    """
    return {
        token
        for token in tokens
        if not token.isdigit() and token != unit and token not in POLICY_STOPWORDS
    }


def _is_policy_grounded(policy_answer, policy_text):
    """Indica si cada número de la parte de ``policies`` viene de la política.

    Un número está fundamentado si alguna cláusula de la política lo trae
    seguido de la misma palabra y comparte al menos una palabra con contenido
    con la cláusula de la respuesta que lo contiene.

    :param policy_answer: Lo que respondió ``policies``
    :type policy_answer: str
    :param policy_text: Texto de la política consultada, o ``None``
    :type policy_text: str or None
    :return: ``True`` si todos los números están fundamentados
    :rtype: bool
    """
    if policy_text is None:
        return False
    policy_clauses = _clauses(policy_text)
    for clause in _clauses(policy_answer):
        for number, unit in _numbers(clause):
            context = _content_words(clause, unit)
            if not any(
                _has_number(policy_clause, number, unit)
                and context & _content_words(policy_clause, unit)
                for policy_clause in policy_clauses
            ):
                return False
    return True


def _replace_policy_part(answer, policy_answer, policy_text):
    """Cambia solo la parte de ``policies`` por la cita de la política.

    :param answer: Respuesta completa
    :type answer: str
    :param policy_answer: Lo que respondió ``policies``
    :type policy_answer: str
    :param policy_text: Texto de la política consultada, o ``None``
    :type policy_text: str or None
    :return: La respuesta con la parte de ``policies`` reemplazada
    :rtype: str
    """
    if policy_text is None:
        quoted = NO_POLICY_ANSWER
    else:
        quoted = POLICY_ANSWER_PREFIX + policy_text
    if not policy_answer or policy_answer not in answer:
        return quoted
    # La parte de ``policies`` va al final: se cambia su última aparición.
    head, _, tail = answer.rpartition(policy_answer)
    return head + quoted + tail


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
    if _has_internal_message(answer):
        return "internal_message", HELP_ANSWER
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
    policy_answer=None,
    policy_text=None,
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
    :param policy_answer: Lo que respondió ``policies``, tal como aparece dentro
        de ``answer``; ``None`` apaga la regla de fundamento de políticas
    :type policy_answer: str or None
    :param policy_text: Texto de la política consultada; ``None`` si no hubo
        consulta (la parte de ``policies`` siempre se reemplaza)
    :type policy_text: str or None
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
    outcome = REVIEW_PASSED
    if policy_answer is not None and not _is_policy_grounded(
        str(policy_answer), policy_text
    ):
        # Solo cambia la parte de ``policies``: el resto sigue revisándose.
        text = _replace_policy_part(text, str(policy_answer), policy_text)
        outcome = REVIEW_REPLACED
        audit("output_reviewed", REVIEW_REPLACED, rule="policy_ungrounded")
    redacted, types = _redact_pii(text, audit)
    if types and outcome == REVIEW_PASSED:
        outcome = REVIEW_REDACTED
        audit("output_reviewed", REVIEW_REDACTED, rule="pii")
    return ReviewResult(
        answer=_cut(redacted, max_chars), review=outcome, pii_types=types
    )


def _redact_pii(text, audit):
    """Tapa los datos personales de la salida y audita ``pii_detected``.

    :param text: Respuesta a revisar
    :type text: str
    :param audit: Función ``(action, outcome, **campos)``
    :type audit: collections.abc.Callable
    :return: El texto tapado y los tipos encontrados, en orden
    :rtype: tuple[str, list[str]]
    """
    redacted, found = pii.redact(text)
    types = pii.ordered_types(found)
    if types:
        audit(
            "pii_detected",
            REVIEW_REDACTED,
            where=OUTPUT_WHERE,
            types=types,
            count=sum(found.values()),
        )
    return redacted, types
