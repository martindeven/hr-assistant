"""Supervisor: decide con reglas qué especialistas responden una pregunta.

No usa modelo. Las reglas no distinguen mayúsculas ni acentos.
"""

import re

from app.text import contains_word, fold

DIRECTORY = "directory"
POLICIES = "policies"
SPECIALIST_ORDER = (DIRECTORY, POLICIES)
EMPLOYEE_ID_PATTERN = re.compile(r"(?<![a-z0-9])emp-\d{4}(?!\d)")


def mentions_directory(question, departments):
    """Indica si la pregunta trae un id ``EMP-####`` o un departamento que existe.

    :param question: Pregunta de la persona
    :type question: str
    :param departments: Departamentos del directorio
    :type departments: list[str]
    :return: ``True`` si la pregunta es del directorio
    :rtype: bool
    """
    folded = fold(question)
    if EMPLOYEE_ID_PATTERN.search(folded):
        return True
    return any(contains_word(folded, fold(name)) for name in departments)


def plan(question, departments, policy_book):
    """Elige los especialistas de una pregunta, en el orden en que corren.

    - Id de empleado o departamento: ``directory``.
    - Palabra clave de una política: ``policies``.
    - Las dos cosas: ``directory`` y después ``policies``.
    - Ninguna: ``directory``, que contesta con la ayuda.

    :param question: Pregunta de la persona
    :type question: str
    :param departments: Departamentos del directorio
    :type departments: list[str]
    :param policy_book: Políticas de RH
    :type policy_book: app.policies.PolicyBook
    :return: Los nombres de los especialistas
    :rtype: list[str]
    """
    chosen = []
    if mentions_directory(question, departments):
        chosen.append(DIRECTORY)
    if policy_book.find_keyword(question) is not None:
        chosen.append(POLICIES)
    return chosen or [DIRECTORY]


def restrict(chosen, permissions):
    """Deja solo los especialistas que el cliente tiene permitidos.

    :param chosen: Especialistas que eligió el supervisor
    :type chosen: list[str]
    :param permissions: Permisos del cliente
    :type permissions: set[str]
    :return: Los especialistas permitidos, en el mismo orden; vacía si ninguno
    :rtype: list[str]
    """
    return [name for name in chosen if name in permissions]
