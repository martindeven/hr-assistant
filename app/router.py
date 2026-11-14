"""Enrutador de costo: una regla fija elige el modelo de cada pregunta.

No usa otro modelo: ante la duda (pregunta larga o palabra clave) manda la
pregunta al modelo capaz, que es más caro pero no baja la calidad.
"""

import unicodedata
from dataclasses import dataclass

ROUTE_DISABLED = "routing_disabled"
ROUTE_LONG_QUESTION = "long_question"
ROUTE_KEYWORD_PREFIX = "keyword:"
ROUTE_SIMPLE = "simple"
KEYWORDS = ("compara", "resume", "explica", "por qué", "analiza", "todos")


@dataclass(frozen=True)
class Route:
    """Modelo elegido para una pregunta y el motivo.

    :param model: Nombre del modelo elegido
    :type model: str
    :param reason: Motivo del enrutador (por ejemplo ``simple``)
    :type reason: str
    """

    model: str
    reason: str


def _fold(text):
    """Pasa un texto a minúsculas y le quita los acentos.

    :param text: Texto original
    :type text: str
    :return: El texto sin mayúsculas ni acentos
    :rtype: str
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


FOLDED_KEYWORDS = tuple((keyword, _fold(keyword)) for keyword in KEYWORDS)


def choose_route(question, settings):
    """Elige el modelo de una pregunta con una regla fija.

    En orden: ``routing_disabled`` si ``ROUTING=false``; ``long_question`` si
    la pregunta sin espacios sobrantes pasa de ``ROUTER_MAX_CHARS``;
    ``keyword:<palabra>`` si contiene una palabra clave (sin distinguir
    mayúsculas ni acentos); y si no, ``simple`` con el modelo barato.

    :param question: Pregunta de la persona
    :type question: str
    :param settings: Configuración con los modelos y la regla
    :type settings: app.config.Settings
    :return: El modelo elegido y el motivo
    :rtype: Route
    """
    if not settings.routing:
        return Route(model=settings.model_large, reason=ROUTE_DISABLED)
    text = question.strip()
    if len(text) > settings.router_max_chars:
        return Route(model=settings.model_large, reason=ROUTE_LONG_QUESTION)
    folded = _fold(text)
    for keyword, folded_keyword in FOLDED_KEYWORDS:
        if folded_keyword in folded:
            return Route(
                model=settings.model_large, reason=ROUTE_KEYWORD_PREFIX + keyword
            )
    return Route(model=settings.model_small, reason=ROUTE_SIMPLE)
