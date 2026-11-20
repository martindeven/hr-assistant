"""Agente ``guard``: revisa la entrada antes de que llegue a cualquier otro agente.

Es el único lugar donde se revisa la pregunta y el historial (datos personales e
inyección de instrucciones). No usa modelo: solo reglas. Primero busca
inyección en la pregunta y en **todo** el historial (también los mensajes
``assistant``, que manda el cliente); después busca datos personales y, según
``PII_INPUT_MODE``, los tapa o bloquea la petición. Los eventos ``audit`` llevan
el id de la regla o los tipos y el conteo, nunca el texto.
"""

from collections import Counter
from dataclasses import dataclass, field

from app import injection, pii

GUARD_AGENT = "guard"
BLOCKED_STATUS = 400
INJECTION_DETAIL = "request blocked by policy"
SENSITIVE_DETAIL = "request contains sensitive data"
PII_MODE_REDACT = "redact"
PII_MODE_BLOCK = "block"
PII_MODE_OFF = "off"
INPUT_WHERE = "input"


class GuardBlocked(Exception):
    """El guard bloquea la petición: la API responde ``400`` con ``detail``.

    :param detail: Texto fijo del cuerpo ``400`` (nunca el texto de la pregunta)
    :type detail: str
    """

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class GuardResult:
    """Entrada ya revisada que siguen usando los demás agentes.

    :param question: Pregunta, con los datos personales tapados
    :type question: str
    :param history: Historial (``role`` y ``content``), con los datos tapados
    :type history: list[dict]
    :param pii_types: Tipos de dato personal encontrados, sin repetir y en orden
    :type pii_types: list[str]
    """

    question: str
    history: list = field(default_factory=list)
    pii_types: list = field(default_factory=list)


def _noop_audit(action, outcome, **fields):
    """Auditor que no hace nada, para usar el guard sin log."""
    del action, outcome, fields


class Guard:
    """Revisa la pregunta y el historial con la configuración de seguridad.

    :param settings: Configuración con ``pii_input_mode`` e ``injection_guard``
    :type settings: app.config.Settings
    """

    def __init__(self, settings):
        self._settings = settings

    def check(self, question, history, audit=None):
        """Revisa una petición y devuelve la entrada que pueden usar los agentes.

        :param question: Pregunta de la persona
        :type question: str
        :param history: Historial con ``role`` y ``content``
        :type history: list[dict]
        :param audit: Función ``(action, outcome, **campos)`` que audita los
            hallazgos (``pii_detected``, ``injection_blocked``); opcional
        :type audit: collections.abc.Callable or None
        :return: La pregunta y el historial revisados y los tipos encontrados
        :rtype: GuardResult
        :raises GuardBlocked: Si la petición se debe responder con ``400``
        """
        audit = audit or _noop_audit
        messages = [dict(message) for message in history]
        if self._settings.injection_guard:
            self._check_injection(question, messages, audit)
        if self._settings.pii_input_mode == PII_MODE_OFF:
            return GuardResult(question=question, history=messages, pii_types=[])
        return self._check_pii(question, messages, audit)

    @staticmethod
    def _check_injection(question, messages, audit):
        """Bloquea si la pregunta o algún mensaje cumple una regla de inyección.

        :param question: Pregunta de la persona
        :type question: str
        :param messages: Historial copiado
        :type messages: list[dict]
        :param audit: Función que audita ``injection_blocked``
        :type audit: collections.abc.Callable
        :raises GuardBlocked: Con ``request blocked by policy``
        """
        texts = [question, *(str(message.get("content", "")) for message in messages)]
        for text in texts:
            rule = injection.match(text)
            if rule is not None:
                audit("injection_blocked", "blocked", rule=rule)
                raise GuardBlocked(INJECTION_DETAIL)

    def _check_pii(self, question, messages, audit):
        """Tapa (o bloquea) los datos personales de la pregunta y del historial.

        :param question: Pregunta de la persona
        :type question: str
        :param messages: Historial copiado; se tapa en su lugar
        :type messages: list[dict]
        :param audit: Función que audita ``pii_detected``
        :type audit: collections.abc.Callable
        :return: La entrada tapada y los tipos encontrados
        :rtype: GuardResult
        :raises GuardBlocked: Con ``request contains sensitive data`` en modo
            ``block``
        """
        total = Counter()
        clean_question, found = pii.redact(question)
        total.update(found)
        for message in messages:
            content, found = pii.redact(str(message.get("content", "")))
            message["content"] = content
            total.update(found)
        types = pii.ordered_types(total)
        if not types:
            return GuardResult(question=question, history=messages, pii_types=[])
        blocked = self._settings.pii_input_mode == PII_MODE_BLOCK
        audit(
            "pii_detected",
            "blocked" if blocked else "redacted",
            where=INPUT_WHERE,
            types=types,
            count=sum(total.values()),
        )
        if blocked:
            raise GuardBlocked(SENSITIVE_DETAIL)
        return GuardResult(question=clean_question, history=messages, pii_types=types)
