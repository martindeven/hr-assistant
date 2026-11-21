"""Agente con bucle de tope que usa herramientas de solo lectura."""

import json
from dataclasses import dataclass, field, replace

from app.llm import (
    DIRECTORY_SPECIALIST,
    INVALID_JSON_MESSAGE,
    MISSING_ARGUMENT_TEMPLATE,
    MISSING_REPLY_KEYS_MESSAGE,
    SYSTEM_ROLE,
    TOOL_DENIED_TEMPLATE,
    TOOL_RESULT_PREFIX,
)
from app.specialists import ARGUMENT_VALIDATORS, Tool

STEP_CAP_ANSWER = (
    "No pude completar la respuesta. Intenta con una pregunta más concreta."
)


# Es un registro plano e inmutable del resultado: cada conteo es un campo que se
# lee por nombre en la API y en el log, así que pasa del límite de pylint.
@dataclass(frozen=True)
class AgentResult:  # pylint: disable=too-many-instance-attributes
    """Resultado de una pregunta al agente.

    :param answer: Respuesta final en español
    :type answer: str
    :param steps: Veces que se llamó al modelo
    :type steps: int
    :param tools_used: Herramientas ejecutadas, en orden
    :type tools_used: list[str]
    :param input_tokens: Tokens de entrada sumados en todas las llamadas
    :type input_tokens: int
    :param output_tokens: Tokens de salida sumados en todas las llamadas
    :type output_tokens: int
    :param stopped: ``True`` si el agente se detuvo por llegar al tope de pasos
    :type stopped: bool
    :param cached_calls: Llamadas que el gateway sirvió de su caché
    :type cached_calls: int
    :param billed_input_tokens: Tokens de entrada de las llamadas no cacheadas
    :type billed_input_tokens: int
    :param billed_output_tokens: Tokens de salida de las llamadas no cacheadas
    :type billed_output_tokens: int
    :param tool_results: Resultados de las herramientas ejecutadas, como JSON
        (el reviewer los usa como evidencia)
    :type tool_results: list[str]
    """

    answer: str
    steps: int
    tools_used: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: bool = False
    cached_calls: int = 0
    billed_input_tokens: int = 0
    billed_output_tokens: int = 0
    tool_results: list = field(default_factory=list)


@dataclass
class _Usage:
    """Tokens y llamadas cacheadas que se van sumando en una corrida."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_calls: int = 0
    billed_input_tokens: int = 0
    billed_output_tokens: int = 0

    def add(self, completion):
        """Suma una respuesta del modelo; las cacheadas no se facturan.

        :param completion: Respuesta del modelo
        :type completion: app.llm.Completion
        """
        self.input_tokens += completion.input_tokens
        self.output_tokens += completion.output_tokens
        if getattr(completion, "cached", False):
            self.cached_calls += 1
        else:
            self.billed_input_tokens += completion.input_tokens
            self.billed_output_tokens += completion.output_tokens

    def as_fields(self):
        """Devuelve los conteos como argumentos de ``AgentResult``.

        :return: Los conteos por nombre de campo
        :rtype: dict[str, int]
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_calls": self.cached_calls,
            "billed_input_tokens": self.billed_input_tokens,
            "billed_output_tokens": self.billed_output_tokens,
        }


class ProtocolError(ValueError):
    """La respuesta del modelo no cumple el protocolo o la herramienta falló."""


class ToolDenied(ProtocolError):
    """El modelo pidió una herramienta que no es de este especialista.

    :param tool: Nombre de la herramienta pedida
    :type tool: object
    """

    def __init__(self, tool):
        super().__init__(TOOL_DENIED_TEMPLATE.format(tool=tool))
        self.tool = tool


def _directory_tools(directory):
    """Herramientas del directorio, para usar ``Agent`` como en la sesión 1.

    :param directory: Directorio de empleados de solo lectura
    :type directory: app.directory.EmployeeDirectory
    :return: Las herramientas por nombre
    :rtype: dict[str, app.specialists.Tool]
    """
    return {
        "get_employee": Tool(
            "get_employee",
            directory.get_employee,
            "employee_id",
            ARGUMENT_VALIDATORS["employee_id"],
        ),
        "search_by_department": Tool(
            "search_by_department",
            directory.search_by_department,
            "department",
            ARGUMENT_VALIDATORS["department"],
        ),
    }


class Agent:
    """Agente con bucle de tope que solo ejecuta las herramientas de su especialista.

    Sin ``specialist`` es el agente de siempre: las dos herramientas del
    directorio y sin mensaje ``system`` propio.

    :param model: Modelo que cumple la interfaz ``complete(messages)``
    :type model: app.llm.LanguageModel
    :param directory: Directorio de empleados de solo lectura (solo se usa sin
        ``specialist``)
    :type directory: app.directory.EmployeeDirectory or None
    :param max_steps: Tope de llamadas al modelo
    :type max_steps: int
    :param tracer: Trazas opcionales; por omisión no se traza nada
    :type tracer: app.tracing.Tracer or None
    :param specialist: Especialista (nombre, prompt y herramientas)
    :type specialist: app.specialists.Specialist or None
    :param trace_loop: Si el bucle queda como nodo ``agent.run``; el pipeline
        lo apaga porque el nodo del especialista ya lo abre ``StepRecorder``
    :type trace_loop: bool
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        model,
        directory,
        max_steps,
        tracer=None,
        *,
        specialist=None,
        trace_loop=True,
    ):
        self._max_steps = max_steps
        self._tracer = tracer
        self._complete = self._wrap_model(model)
        self._traced_loop = self._loop
        if specialist is None:
            self.name = DIRECTORY_SPECIALIST
            self._system_prompt = None
            tools = _directory_tools(directory)
        else:
            self.name = specialist.name
            self._system_prompt = specialist.system_prompt
            tools = dict(specialist.tools)
        self._tools = tools
        if tracer is not None:
            # Con el Tracer apagado, ``wrap`` devuelve la misma función.
            if trace_loop:
                self._traced_loop = tracer.wrap(
                    self._loop, name="agent.run", run_type="chain"
                )
            self._tools = {
                name: replace(
                    tool,
                    function=tracer.wrap(tool.function, name=name, run_type="tool"),
                )
                for name, tool in tools.items()
            }

    def _wrap_model(self, model):
        """Devuelve el ``complete`` del modelo, trazado si hay ``Tracer``.

        :param model: Modelo que cumple la interfaz ``complete(messages)``
        :type model: app.llm.LanguageModel
        :return: La función que llama al modelo
        :rtype: collections.abc.Callable
        """
        if self._tracer is None:
            return model.complete
        return self._tracer.wrap(model.complete, name="model.complete", run_type="llm")

    def run(self, messages, on_tool_call=None, model=None, on_tool_denied=None):
        """Responde una conversación, llamando herramientas cuando el modelo lo pide.

        Si el especialista tiene prompt propio, se antepone como mensaje
        ``system``.

        :param messages: Conversación con ``role`` y ``content``; no se modifica
        :type messages: list[dict]
        :param on_tool_call: Función que recibe ``(nombre, args)`` de cada
            herramienta que pide el modelo, antes de ejecutarla (aunque luego
            se niegue)
        :type on_tool_call: collections.abc.Callable or None
        :param model: Modelo de esta corrida; por omisión el del constructor
        :type model: app.llm.LanguageModel or None
        :param on_tool_denied: Función que recibe ``(nombre, args)`` de cada
            herramienta ajena o inexistente que pide el modelo (no se ejecuta)
        :type on_tool_denied: collections.abc.Callable or None
        :return: La respuesta, los pasos, las herramientas, los tokens y si se
            llegó al tope
        :rtype: AgentResult
        """
        conversation = [dict(message) for message in messages]
        if self._system_prompt is not None:
            conversation.insert(
                0, {"role": SYSTEM_ROLE, "content": self._system_prompt}
            )
        hooks = (on_tool_call, on_tool_denied)
        if model is None:
            return self._traced_loop(conversation, hooks)
        return self._traced_loop(conversation, hooks, self._wrap_model(model))

    def _loop(self, messages, hooks, complete=None):
        """Bucle con tope del agente (la corrida que se traza).

        :param messages: Conversación con ``role`` y ``content``; no se modifica
        :type messages: list[dict]
        :param hooks: Funciones ``(on_tool_call, on_tool_denied)``; cada una
            puede ser ``None``
        :type hooks: tuple
        :param complete: Función que llama al modelo; por omisión la del
            constructor
        :type complete: collections.abc.Callable or None
        :return: El resultado de la corrida
        :rtype: AgentResult
        """
        complete = complete or self._complete
        conversation = [dict(message) for message in messages]
        steps = 0
        usage = _Usage()
        tools_used = []
        tool_results = []
        while steps < self._max_steps:
            completion = complete(conversation)
            steps += 1
            usage.add(completion)
            try:
                reply = self._parse(completion.content)
                if "answer" in reply:
                    return AgentResult(
                        answer=str(reply["answer"]),
                        steps=steps,
                        tools_used=tools_used,
                        tool_results=tool_results,
                        **usage.as_fields(),
                    )
                result = self._call_tool(reply, hooks)
                tools_used.append(reply["tool"])
            except ProtocolError as error:
                result = {"error": str(error)}
            encoded = json.dumps(result, ensure_ascii=False)
            tool_results.append(encoded)
            conversation.append({"role": "assistant", "content": completion.content})
            conversation.append(
                {"role": "user", "content": TOOL_RESULT_PREFIX + encoded}
            )
        return AgentResult(
            answer=STEP_CAP_ANSWER,
            steps=steps,
            tools_used=tools_used,
            stopped=True,
            tool_results=tool_results,
            **usage.as_fields(),
        )

    def _call_tool(self, reply, hooks):
        """Avisa a los hooks y ejecuta la herramienta pedida si es del especialista.

        :param reply: Objeto con ``tool`` y ``args``
        :type reply: dict
        :param hooks: Funciones ``(on_tool_call, on_tool_denied)``
        :type hooks: tuple
        :return: El resultado de la herramienta
        :rtype: dict or list
        :raises ProtocolError: Si la herramienta es ajena, no existe, faltan
            argumentos o el argumento no es válido
        """
        on_tool_call, on_tool_denied = hooks
        name = reply.get("tool")
        # Se avisa de toda herramienta pedida, aunque no exista (sesión 4).
        if on_tool_call is not None:
            on_tool_call(name, reply.get("args"))
        if not isinstance(name, str) or name not in self._tools:
            if on_tool_denied is not None:
                on_tool_denied(name, reply.get("args"))
            raise ToolDenied(name)
        return self._execute(reply)

    @staticmethod
    def _parse(content):
        """Interpreta la respuesta del modelo como un objeto del protocolo.

        :param content: Texto devuelto por el modelo
        :type content: str
        :return: Objeto con ``answer`` o con ``tool`` y ``args``
        :rtype: dict
        :raises ProtocolError: Si no es JSON o no tiene ``answer`` ni ``tool``
        """
        try:
            reply = json.loads(content)
        except (TypeError, ValueError) as error:
            raise ProtocolError(INVALID_JSON_MESSAGE) from error
        if not isinstance(reply, dict) or not (
            "answer" in reply or "tool" in reply
        ):
            raise ProtocolError(MISSING_REPLY_KEYS_MESSAGE)
        return reply

    def _execute(self, reply):
        """Ejecuta la herramienta pedida con sus argumentos.

        :param reply: Objeto con ``tool`` y ``args``
        :type reply: dict
        :return: El resultado de la herramienta
        :rtype: dict or list
        :raises ProtocolError: Si la herramienta no existe, faltan argumentos o
            el argumento no pasa su validador (en ese caso no se leen datos)
        """
        name = reply.get("tool")
        if name not in self._tools:
            raise ToolDenied(name)
        tool = self._tools[name]
        args = reply.get("args")
        if not isinstance(args, dict) or not isinstance(args.get(tool.argument), str):
            raise ProtocolError(
                MISSING_ARGUMENT_TEMPLATE.format(name=name, argument=tool.argument)
            )
        problem = tool.validator(args[tool.argument])
        if problem:
            raise ProtocolError(str(problem))
        return tool.function(args[tool.argument])
