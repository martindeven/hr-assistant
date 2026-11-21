"""Reglas contra la inyección de instrucciones (punto 3.2 de la especificación).

Es el único lugar del código con las reglas. Se aplican sobre
:func:`app.text.fold`, así que no distinguen mayúsculas ni acentos. Son frases,
no palabras sueltas, para no bloquear preguntas normales de RH (por ejemplo
«permiso» o «sistema de nómina»). Quien llama solo recibe el id de la regla:
el texto nunca sale de aquí.
"""

import re

from app.text import fold

# Pares ``(id, expresión regular)`` sobre el texto normalizado con ``fold``.
RULES = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignora|olvida)\s+(?:(?:las|tus|todas|sus|mis)\s+)+"
            r"(?:\w+\s+)?instrucciones\b"
        ),
    ),
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|forget)\s+(?:(?:all|previous|prior|the|above|your|any)\s+)+"
            r"instructions\b"
        ),
    ),
    ("system_prompt", re.compile(r"\bprompt\s+del\s+sistema\b")),
    ("system_prompt", re.compile(r"\bsystem\s+prompt\b")),
    ("system_prompt", re.compile(r"\brevela\s+tu\s+prompt\b")),
    ("role_override", re.compile(r"\beres\s+ahora\b")),
    ("role_override", re.compile(r"\byou\s+are\s+now\b")),
    ("developer_mode", re.compile(r"\bmodo\s+desarrollador\b")),
    ("developer_mode", re.compile(r"\bdeveloper\s+mode\b")),
    ("jailbreak", re.compile(r"\bjailbreak")),
    ("role_marker", re.compile(r"<\|")),
    ("role_marker", re.compile(r"\[/?inst\]")),
    ("role_marker", re.compile(r"#{3}\s*system\b")),
)


def match(text):
    """Dice qué regla de inyección cumple un texto.

    :param text: Texto a revisar (pregunta o mensaje del historial)
    :type text: str
    :return: El id de la regla, o ``None`` si no cumple ninguna
    :rtype: str or None
    """
    folded = fold(text)
    for rule_id, pattern in RULES:
        if pattern.search(folded):
            return rule_id
    return None
