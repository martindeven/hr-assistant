"""Normalización de texto compartida: sin mayúsculas, sin acentos, sin espacios dobles.

La usan el supervisor, las políticas y el guard para que "sin distinguir
mayúsculas ni acentos" signifique lo mismo en todo el producto.
"""

import re
import unicodedata

WHITESPACE_PATTERN = re.compile(r"\s+")


def fold(text):
    """Pasa un texto a minúsculas, le quita los acentos y colapsa los espacios.

    :param text: Texto original
    :type text: str
    :return: El texto normalizado
    :rtype: str
    """
    decomposed = unicodedata.normalize("NFKD", str(text).casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return WHITESPACE_PATTERN.sub(" ", plain).strip()


def contains_word(folded_text, folded_phrase):
    """Indica si una frase aparece como palabra completa dentro de un texto.

    Ambos textos deben venir ya normalizados con :func:`fold`.

    :param folded_text: Texto donde buscar
    :type folded_text: str
    :param folded_phrase: Frase a buscar
    :type folded_phrase: str
    :return: ``True`` si la frase aparece rodeada de límites de palabra
    :rtype: bool
    """
    if not folded_phrase:
        return False
    pattern = r"(?<!\w)" + re.escape(folded_phrase) + r"(?!\w)"
    return re.search(pattern, folded_text) is not None
