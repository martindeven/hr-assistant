"""Agente con bucle de tope que usa herramientas de solo lectura."""

import json
from dataclasses import dataclass, field

from app.llm import TOOL_RESULT_PREFIX

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


class Agent:
    """Agente único con dos herramientas sobre el directorio de empleados.

    :param model: Modelo que cumple la interfaz ``complete(messages)``
    :type model: app.llm.LanguageModel
    :param directory: Directorio de empleados de solo lectura
    :type directory: app.directory.EmployeeDirectory
    :param max_steps: Tope de llamadas al modelo
    :type max_steps: int
    :param tracer: Trazas opcionales; por omisión no se traza nada
    :type tracer: app.tracing.Tracer or None
    """

    def __init__(self, model, directory, max_steps, tracer=None):
        self._max_steps = max_steps
        self._tracer = tracer
        self._complete = self._wrap_model(model)
        self._traced_loop = self._loop
        self._tools = {
            "get_employee": (directory.get_employee, "employee_id"),
            "search_by_department": (directory.search_by_department, "department"),
        }
        if tracer is not None:
            # Con el Tracer apagado, ``wrap`` devuelve la misma función.
            self._traced_loop = tracer.wrap(
                self._loop, name="agent.run", run_type="chain"
            )
            self._tools = {
                name: (tracer.wrap(function, name=name, run_type="tool"), argument)
                for name, (function, argument) in self._tools.items()
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

    def run(self, messages, on_tool_call=None, model=None):
        """Responde una conversación, llamando herramientas cuando el modelo lo pide.

        :param messages: Conversación con ``role`` y ``content``; no se modifica
        :type messages: list[dict]
        :param on_tool_call: Función que recibe ``(nombre, args)`` de cada
            herramienta que pide el modelo, antes de ejecutarla
        :type on_tool_call: collections.abc.Callable or None
        :param model: Modelo de esta corrida; por omisión el del constructor
        :type model: app.llm.LanguageModel or None
        :return: La respuesta, los pasos, las herramientas, los tokens y si se
            llegó al tope
        :rtype: AgentResult
        """
        if model is None:
            return self._traced_loop(messages, on_tool_call)
        return self._traced_loop(messages, on_tool_call, self._wrap_model(model))

    def _loop(self, messages, on_tool_call, complete=None):
        """Bucle con tope del agente (la corrida que se traza).

        :param messages: Conversación con ``role`` y ``content``; no se modifica
        :type messages: list[dict]
        :param on_tool_call: Función que recibe ``(nombre, args)``, o ``None``
        :type on_tool_call: collections.abc.Callable or None
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
                        **usage.as_fields(),
                    )
                if on_tool_call is not None:
                    on_tool_call(reply.get("tool"), reply.get("args"))
                result = self._execute(reply)
                tools_used.append(reply["tool"])
            except ProtocolError as error:
                result = {"error": str(error)}
            conversation.append({"role": "assistant", "content": completion.content})
            conversation.append(
                {
                    "role": "user",
                    "content": TOOL_RESULT_PREFIX
                    + json.dumps(result, ensure_ascii=False),
                }
            )
        return AgentResult(
            answer=STEP_CAP_ANSWER,
            steps=steps,
            tools_used=tools_used,
            stopped=True,
            **usage.as_fields(),
        )

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
            message = "La respuesta del modelo no es JSON válido."
            raise ProtocolError(message) from error
        if not isinstance(reply, dict) or not (
            "answer" in reply or "tool" in reply
        ):
            raise ProtocolError("La respuesta debe tener 'answer' o 'tool'.")
        return reply

    def _execute(self, reply):
        """Ejecuta la herramienta pedida con sus argumentos.

        :param reply: Objeto con ``tool`` y ``args``
        :type reply: dict
        :return: El resultado de la herramienta
        :rtype: dict or list
        :raises ProtocolError: Si la herramienta no existe o faltan argumentos
        """
        name = reply.get("tool")
        if name not in self._tools:
            raise ProtocolError(f"La herramienta {name!r} no existe.")
        function, argument = self._tools[name]
        args = reply.get("args")
        if not isinstance(args, dict) or not isinstance(args.get(argument), str):
            raise ProtocolError(
                f"La herramienta {name} necesita el argumento '{argument}'."
            )
        return function(args[argument])
