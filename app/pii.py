"""Detector de datos personales (PII) del punto 3.1 de la especificación.

Reconoce correo, teléfono, CURP, RFC, NSS, tarjeta de crédito y CLABE, y los
tapa con su etiqueta (``[EMAIL]``, ``[PHONE]``, ...). Los patrones se aplican en
un orden fijo sobre el texto ya tapado, así que lo tapado no se vuelve a revisar
(las etiquetas no tienen dígitos ni ``@``). Los números llevan límites de dígito
para no tapar ids ``EMP-####``, fechas, folios cortos ni montos.

Este módulo nunca escribe en el log: devuelve solo el texto tapado y cuántos
datos de cada tipo encontró.
"""

import re
from collections import Counter

CREDIT_CARD = "credit_card"
CREDIT_CARD_MIN_DIGITS = 13
CREDIT_CARD_MAX_DIGITS = 19
LUHN_MODULUS = 10
LUHN_DOUBLE_LIMIT = 9

# Pares ``(tipo, expresión regular)`` en el orden en que se aplican. El orden
# importa: la CURP va antes que el RFC (la CURP empieza como un RFC) y la CLABE
# (18 dígitos) antes que la tarjeta, aunque pase la prueba de Luhn.
PATTERNS = (
    (
        "email",
        re.compile(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
        ),
    ),
    (
        "curp",
        re.compile(
            r"(?<![A-Za-z0-9])[A-Z][AEIOUX][A-Z]{2}\d{6}[HMX][A-Z]{5}[A-Z0-9]\d"
            r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        "rfc",
        re.compile(
            r"(?<![A-Za-z0-9&])[A-Z&]{3,4}\d{6}[A-Z0-9]{3}(?![A-Za-z0-9])",
            re.IGNORECASE,
        ),
    ),
    ("clabe", re.compile(r"(?<!\d)\d{18}(?!\d)")),
    (CREDIT_CARD, re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")),
    ("nss", re.compile(r"(?<!\d)\d{11}(?!\d)")),
    (
        "phone",
        re.compile(r"(?<![\d+])(?:\+?52[ .-]?)?\d(?:[ .-]?\d){9}(?!\d)"),
    ),
)


def label(kind):
    """Etiqueta con la que se tapa un tipo de dato.

    :param kind: Tipo de dato (``email``, ``phone``, ...)
    :type kind: str
    :return: La etiqueta, por ejemplo ``[EMAIL]``
    :rtype: str
    """
    return f"[{kind.upper()}]"


def passes_luhn(digits):
    """Indica si una cadena de dígitos pasa la prueba de Luhn.

    :param digits: Solo dígitos
    :type digits: str
    :return: ``True`` si la suma de control es válida
    :rtype: bool
    """
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value > LUHN_DOUBLE_LIMIT:
                value -= LUHN_DOUBLE_LIMIT
        total += value
    return total % LUHN_MODULUS == 0


def _is_credit_card(text):
    """Indica si un texto con dígitos y separadores es una tarjeta válida.

    :param text: Texto que encontró el patrón de tarjeta
    :type text: str
    :return: ``True`` si tiene de 13 a 19 dígitos y pasa la prueba de Luhn
    :rtype: bool
    """
    digits = "".join(char for char in text if char.isdigit())
    if not CREDIT_CARD_MIN_DIGITS <= len(digits) <= CREDIT_CARD_MAX_DIGITS:
        return False
    return passes_luhn(digits)


def redact(text):
    """Tapa los datos personales de un texto con su etiqueta (``[EMAIL]``, ...).

    :param text: Texto a revisar
    :type text: str
    :return: El texto tapado y cuántos datos de cada tipo se encontraron
    :rtype: tuple[str, collections.Counter]
    """
    found = Counter()
    result = str(text)
    for kind, pattern in PATTERNS:

        def replace(match, kind=kind):
            """Tapa un hallazgo y lo cuenta; una tarjeta falsa queda igual."""
            if kind == CREDIT_CARD and not _is_credit_card(match.group(0)):
                return match.group(0)
            found[kind] += 1
            return label(kind)

        result = pattern.sub(replace, result)
    return result, found


def ordered_types(found):
    """Tipos encontrados, sin repetir y en el orden de ``PATTERNS``.

    :param found: Conteo por tipo
    :type found: collections.Counter
    :return: Los tipos con al menos un hallazgo
    :rtype: list[str]
    """
    return [kind for kind, _ in PATTERNS if found.get(kind)]
