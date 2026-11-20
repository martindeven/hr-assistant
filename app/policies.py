"""Políticas de Recursos Humanos de solo lectura, cargadas una vez desde un JSON."""

import json
from dataclasses import dataclass
from pathlib import Path

from app.text import contains_word, fold

NOT_FOUND_TEMPLATE = "No hay una política sobre el tema {topic!r}."


class PolicyError(ValueError):
    """El archivo de políticas no se puede leer o no tiene la forma esperada."""


@dataclass(frozen=True)
class Policy:
    """Una política de RH.

    :param topic: Tema de la política (por ejemplo ``vacaciones``)
    :type topic: str
    :param keywords: Palabras clave, ya normalizadas con ``fold``
    :type keywords: tuple[str, ...]
    :param text: Texto de la política
    :type text: str
    """

    topic: str
    keywords: tuple
    text: str


def _parse_policy(number, raw):
    """Valida una entrada del archivo de políticas.

    :param number: Número de la entrada, desde 1
    :type number: int
    :param raw: Objeto con ``topic``, ``keywords`` y ``text``
    :type raw: object
    :return: La política
    :rtype: Policy
    :raises PolicyError: Si falta un campo o tiene otro tipo
    """
    if not isinstance(raw, dict):
        raise PolicyError(f"policy {number} must be an object")
    topic, keywords, text = raw.get("topic"), raw.get("keywords"), raw.get("text")
    if not isinstance(topic, str) or not topic.strip():
        raise PolicyError(f"policy {number} needs a 'topic'")
    if not isinstance(text, str) or not text.strip():
        raise PolicyError(f"policy {number} needs a 'text'")
    if not isinstance(keywords, list) or not all(
        isinstance(keyword, str) for keyword in keywords
    ):
        raise PolicyError(f"policy {number} needs a list of 'keywords'")
    return Policy(
        topic=topic.strip(),
        keywords=tuple(fold(keyword) for keyword in keywords if keyword.strip()),
        text=text,
    )


class PolicyBook:
    """Conjunto de políticas de RH en solo lectura.

    :param policies: Políticas en el orden del archivo
    :type policies: list[Policy]
    """

    def __init__(self, policies):
        self._policies = tuple(policies)
        # Cada término (tema o palabra clave) apunta a su política; se ordenan del
        # más largo al más corto para que gane la palabra clave más específica.
        terms = []
        for policy in self._policies:
            for term in {fold(policy.topic), *policy.keywords}:
                terms.append((term, policy))
        self._terms = tuple(sorted(terms, key=lambda item: -len(item[0])))

    @classmethod
    def load(cls, path):
        """Lee las políticas de un archivo JSON (una lista de objetos).

        :param path: Ruta del archivo de políticas
        :type path: str or pathlib.Path
        :return: Las políticas
        :rtype: PolicyBook
        :raises PolicyError: Si el archivo no se puede leer o no es válido
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PolicyError("cannot read the policies file") from error
        if not isinstance(data, list):
            raise PolicyError("the policies file must be a list")
        return cls(_parse_policy(number, raw) for number, raw in enumerate(data, 1))

    def __len__(self):
        """Cuántas políticas hay.

        :return: El número de políticas
        :rtype: int
        """
        return len(self._policies)

    def _find(self, text):
        """Busca la política cuyo término aparece en el texto como palabra completa.

        :param text: Texto donde buscar
        :type text: str
        :return: La política del término más largo, o ``None``
        :rtype: Policy or None
        """
        folded = fold(text)
        for term, policy in self._terms:
            if contains_word(folded, term):
                return policy
        return None

    def find_keyword(self, text):
        """Dice qué tema de política menciona un texto.

        :param text: Texto de la pregunta
        :type text: str
        :return: El tema de la política, o ``None`` si no menciona ninguna
        :rtype: str or None
        """
        policy = self._find(text)
        return policy.topic if policy is not None else None

    def search_policy(self, topic):
        """Busca una política por su tema o por una de sus palabras clave.

        :param topic: Tema que pidió el modelo
        :type topic: str
        :return: ``{"topic", "text"}``, o ``{"error": ...}`` si no hay política
        :rtype: dict
        """
        wanted = fold(topic)
        for term, policy in self._terms:
            if term == wanted:
                return {"topic": policy.topic, "text": policy.text}
        policy = self._find(topic)
        if policy is not None:
            return {"topic": policy.topic, "text": policy.text}
        return {"error": NOT_FOUND_TEMPLATE.format(topic=str(topic))}
