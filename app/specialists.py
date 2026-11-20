"""Especialistas del sistema multiagente y sus herramientas.

Cada especialista tiene su nombre, su prompt de sistema y **solo** sus
herramientas. Cada herramienta lleva su función, el nombre de su argumento y un
validador del argumento (un solo lugar: ``ARGUMENT_VALIDATORS``).
"""

import re
from dataclasses import dataclass

from app.llm import DIRECTORY_SPECIALIST, POLICIES_SPECIALIST, SYSTEM_PROMPTS

EMPLOYEE_ID_RULE = re.compile(r"EMP-[0-9]{4}")
MAX_TEXT_ARGUMENT_CHARS = 60


def accept_any(value):
    """Validador que acepta cualquier argumento de texto.

    Es el validador por omisión mientras ``security`` no agregue las reglas
    (``employee_id``, ``department`` y ``topic``).

    :param value: Argumento que pidió el modelo
    :type value: str
    :return: ``None``: el argumento es válido
    :rtype: str or None
    """
    del value


def validate_employee_id(value):
    """Exige que ``employee_id`` cumpla ``^EMP-\\d{4}$`` (punto 3.2).

    El mensaje de error no repite el argumento: puede traer una inyección.

    :param value: Argumento que pidió el modelo
    :type value: str
    :return: El mensaje de error, o ``None`` si es válido
    :rtype: str or None
    """
    if EMPLOYEE_ID_RULE.fullmatch(value):
        return None
    return "employee_id debe tener la forma EMP-#### (cuatro dígitos)."


def validate_short_text(value):
    """Exige que ``department`` o ``topic`` tengan 60 caracteres o menos.

    :param value: Argumento que pidió el modelo
    :type value: str
    :return: El mensaje de error, o ``None`` si es válido
    :rtype: str or None
    """
    if len(value) <= MAX_TEXT_ARGUMENT_CHARS:
        return None
    return f"El argumento pasa de {MAX_TEXT_ARGUMENT_CHARS} caracteres."


# Validadores por nombre de argumento: el único lugar donde se deciden.
ARGUMENT_VALIDATORS = {
    "employee_id": validate_employee_id,
    "department": validate_short_text,
    "topic": validate_short_text,
}


@dataclass(frozen=True)
class Tool:
    """Herramienta de solo lectura de un especialista.

    :param name: Nombre con el que la pide el modelo
    :type name: str
    :param function: Función que recibe el argumento y devuelve el resultado
    :type function: collections.abc.Callable[[str], object]
    :param argument: Nombre del único argumento
    :type argument: str
    :param validator: Función que devuelve un mensaje de error si el argumento
        no es válido, o ``None`` si lo es
    :type validator: collections.abc.Callable[[str], str or None]
    """

    name: str
    function: object
    argument: str
    validator: object = accept_any


@dataclass(frozen=True)
class Specialist:
    """Especialista: un agente con su prompt y sus herramientas.

    :param name: Nombre del especialista (``directory`` o ``policies``)
    :type name: str
    :param system_prompt: Prompt de sistema que el agente antepone, o ``None``
        si basta el de omisión del modelo (el de ``directory``)
    :type system_prompt: str or None
    :param tools: Herramientas que puede ejecutar, por nombre
    :type tools: dict[str, Tool]
    """

    name: str
    system_prompt: str
    tools: dict


def _tool(name, function, argument):
    """Crea una herramienta con el validador de su argumento.

    :param name: Nombre de la herramienta
    :type name: str
    :param function: Función de la herramienta
    :type function: collections.abc.Callable
    :param argument: Nombre del argumento
    :type argument: str
    :return: La herramienta
    :rtype: Tool
    """
    return Tool(
        name=name,
        function=function,
        argument=argument,
        validator=ARGUMENT_VALIDATORS.get(argument, accept_any),
    )


def build_specialists(directory, policies):
    """Crea los especialistas sobre el directorio y las políticas.

    :param directory: Directorio de empleados de solo lectura
    :type directory: app.directory.EmployeeDirectory
    :param policies: Políticas de RH de solo lectura
    :type policies: app.policies.PolicyBook
    :return: Los especialistas por nombre
    :rtype: dict[str, Specialist]
    """
    directory_tools = (
        _tool("get_employee", directory.get_employee, "employee_id"),
        _tool("search_by_department", directory.search_by_department, "department"),
    )
    policies_tools = (_tool("search_policy", policies.search_policy, "topic"),)
    return {
        # ``directory`` no antepone su prompt: los modelos usan el de
        # ``directory`` cuando la conversación no trae ``system``, así que la
        # conversación queda igual que en las sesiones 1 a 4.
        DIRECTORY_SPECIALIST: Specialist(
            name=DIRECTORY_SPECIALIST,
            system_prompt=None,
            tools={tool.name: tool for tool in directory_tools},
        ),
        POLICIES_SPECIALIST: Specialist(
            name=POLICIES_SPECIALIST,
            system_prompt=SYSTEM_PROMPTS[POLICIES_SPECIALIST],
            tools={tool.name: tool for tool in policies_tools},
        ),
    }
