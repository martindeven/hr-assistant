"""Orquestación de una pregunta: guard, supervisor, especialistas y reviewer.

El pipeline no guarda nada entre peticiones: cada llamada a ``answer`` arma su
propio estado, así que se puede usar desde varios hilos a la vez.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.agent import Agent, AgentResult
from app.audit import audit_args
from app.guard import GUARD_AGENT
from app.llm import HELP_ANSWER, SYSTEM_PROMPTS, ModelUnavailableError
from app.reviewer import REVIEWER_AGENT, review
from app.router import choose_route
from app.supervisor import plan, restrict

SUPERVISOR_AGENT = "supervisor"
ANSWER_SEPARATOR = "\n"
EVIDENCE_ID_PATTERN = re.compile(r"EMP-\d{4}", re.IGNORECASE)
FIXED_TEXTS = (HELP_ANSWER,)


class Forbidden(Exception):
    """El cliente no tiene permiso para ninguno de los especialistas elegidos."""


@dataclass(frozen=True)
class PipelineDependencies:
    """Lo que el pipeline necesita para responder.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param guard: Guard de la entrada
    :type guard: app.guard.Guard
    :param specialists: Especialistas por nombre
    :type specialists: dict[str, app.specialists.Specialist]
    :param departments: Departamentos del directorio (para el supervisor)
    :type departments: list[str]
    :param policy_book: Políticas de RH (para el supervisor)
    :type policy_book: app.policies.PolicyBook
    :param models: Modelos por nombre
    :type models: dict[str, app.llm.LanguageModel]
    :param tracer: Trazas de los agentes
    :type tracer: app.tracing.Tracer
    """

    settings: object
    guard: object
    specialists: dict
    departments: list
    policy_book: object
    models: dict
    tracer: object


@dataclass(frozen=True)
class PipelineResult:  # pylint: disable=too-many-instance-attributes
    """Resultado de una pregunta, listo para la respuesta y el log.

    :param result: Resultado sumado de los especialistas, con la respuesta
        revisada
    :type result: app.agent.AgentResult
    :param route: Ruta que eligió el enrutador
    :type route: app.router.Route
    :param model: Modelo que respondió (el pequeño si hubo respaldo)
    :type model: str
    :param agents: Agentes que corrieron, en orden
    :type agents: list[str]
    :param guardrails: ``pii_input``, ``pii_output`` y ``review``
    :type guardrails: dict
    :param fallback: ``True`` si respondió ``MODEL_SMALL`` por respaldo
    :type fallback: bool
    :param history_sent: Mensajes del historial que se mandaron al modelo
    :type history_sent: int
    """

    result: AgentResult
    route: object
    model: str
    agents: list = field(default_factory=list)
    guardrails: dict = field(default_factory=dict)
    fallback: bool = False
    history_sent: int = 0


def window_history(history, window):
    """Deja solo los últimos ``window`` mensajes del historial del cliente.

    Con ``window`` igual a 0 no se manda historial (``history[-0:]`` daría
    todo).

    :param history: Historial que mandó el cliente
    :type history: list
    :param window: Mensajes que se conservan (``HISTORY_WINDOW``)
    :type window: int
    :return: Los últimos mensajes del historial
    :rtype: list
    """
    if window <= 0:
        return []
    return list(history[-window:])


def combine(results):
    """Junta los resultados de varios especialistas en uno.

    Las respuestas se separan con un salto de línea; pasos, herramientas,
    tokens y llamadas cacheadas se suman.

    :param results: Resultados en el orden en que corrieron
    :type results: list[app.agent.AgentResult]
    :return: El resultado sumado
    :rtype: app.agent.AgentResult
    """
    return AgentResult(
        answer=ANSWER_SEPARATOR.join(result.answer for result in results),
        steps=sum(result.steps for result in results),
        tools_used=[tool for result in results for tool in result.tools_used],
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        stopped=any(result.stopped for result in results),
        cached_calls=sum(result.cached_calls for result in results),
        billed_input_tokens=sum(result.billed_input_tokens for result in results),
        billed_output_tokens=sum(result.billed_output_tokens for result in results),
        tool_results=[text for result in results for text in result.tool_results],
    )


def _evidence_ids(question, result):
    """Ids ``EMP-####`` que vienen de la pregunta o de un resultado de herramienta.

    :param question: Pregunta revisada por el guard
    :type question: str
    :param result: Resultado sumado de los especialistas
    :type result: app.agent.AgentResult
    :return: Los ids en mayúsculas
    :rtype: set[str]
    """
    texts = [question, *result.tool_results]
    return {
        found.upper() for text in texts for found in EVIDENCE_ID_PATTERN.findall(text)
    }


@dataclass(frozen=True)
class _Hooks:
    """Funciones de auditoría de una petición.

    :param audit: Función ``(action, outcome, **campos)``
    :type audit: collections.abc.Callable
    """

    audit: Callable

    def tool_call(self, tool, args):
        """Audita cada herramienta que pide el modelo, antes de revisarla."""
        self.audit("tool_call", "requested", tool=tool, args=audit_args(args))

    def tool_denied(self, tool, args):
        """Audita una herramienta ajena o inexistente (no se ejecuta)."""
        self.audit("tool_denied", "denied", tool=tool, args=audit_args(args))


class Pipeline:
    """Responde una pregunta con todos los agentes, en orden.

    :param deps: Dependencias del pipeline
    :type deps: PipelineDependencies
    """

    def __init__(self, deps):
        self._deps = deps

    def answer(self, question, history, permissions, audit):
        """Responde una pregunta.

        :param question: Pregunta validada
        :type question: str
        :param history: Historial validado, con ``role`` y ``content``
        :type history: list[dict]
        :param permissions: Permisos del cliente
        :type permissions: set[str]
        :param audit: Función ``(action, outcome, **campos)`` de la petición
        :type audit: collections.abc.Callable
        :return: El resultado listo para la respuesta
        :rtype: PipelineResult
        :raises app.guard.GuardBlocked: Si el guard bloquea la petición
        :raises Forbidden: Si el cliente no puede usar ningún especialista elegido
        :raises app.llm.ModelUnavailableError: Si ningún modelo responde
        """
        deps = self._deps
        checked = deps.guard.check(question, history, audit=audit)
        route = choose_route(checked.question, deps.settings)
        chosen = restrict(
            plan(checked.question, deps.departments, deps.policy_book), permissions
        )
        if not chosen:
            audit("forbidden", "denied")
            raise Forbidden()
        sent_history = window_history(checked.history, deps.settings.history_window)
        messages = [dict(message) for message in sent_history]
        messages.append({"role": "user", "content": checked.question})
        result, model, fallback = self._run_with_fallback(
            chosen, messages, route, _Hooks(audit)
        )
        reviewed = review(
            result.answer,
            _evidence_ids(checked.question, result),
            SYSTEM_PROMPTS.values(),
            FIXED_TEXTS,
            max_chars=deps.settings.max_answer_chars,
            audit=audit,
        )
        return PipelineResult(
            result=replace(result, answer=reviewed.answer),
            route=route,
            model=model,
            agents=[GUARD_AGENT, SUPERVISOR_AGENT, *chosen, REVIEWER_AGENT],
            guardrails={
                "pii_input": list(checked.pii_types),
                "pii_output": list(reviewed.pii_types),
                "review": reviewed.review,
            },
            fallback=fallback,
            history_sent=len(sent_history),
        )

    def _run_with_fallback(self, chosen, messages, route, hooks):
        """Corre los especialistas y, si el modelo grande falla, usa el pequeño.

        El respaldo solo aplica si la ruta eligió ``MODEL_LARGE`` y
        ``MODEL_FALLBACK`` está encendido. La pregunta se reinicia desde el
        primer especialista con ``MODEL_SMALL``.

        :param chosen: Especialistas permitidos, en orden
        :type chosen: list[str]
        :param messages: Historial recortado y pregunta
        :type messages: list[dict]
        :param route: Ruta que eligió el enrutador
        :type route: app.router.Route
        :param hooks: Auditoría de la petición
        :type hooks: _Hooks
        :return: El resultado sumado, el modelo que respondió y si hubo respaldo
        :rtype: tuple[app.agent.AgentResult, str, bool]
        :raises app.llm.ModelUnavailableError: Si ningún modelo responde
        """
        settings = self._deps.settings
        try:
            return self._run(chosen, messages, route.model, hooks), route.model, False
        except ModelUnavailableError:
            can_fall_back = (
                settings.model_fallback
                and route.model == settings.model_large
                and settings.model_small != settings.model_large
            )
            if not can_fall_back:
                raise
        hooks.audit(
            "fallback",
            "used",
            from_model=settings.model_large,
            to_model=settings.model_small,
        )
        small = settings.model_small
        return self._run(chosen, messages, small, hooks), small, True

    def _run(self, chosen, messages, model_name, hooks):
        """Corre los especialistas elegidos, uno tras otro, con el mismo modelo.

        :param chosen: Especialistas, en orden
        :type chosen: list[str]
        :param messages: Historial recortado y pregunta
        :type messages: list[dict]
        :param model_name: Modelo de toda la pregunta
        :type model_name: str
        :param hooks: Auditoría de la petición
        :type hooks: _Hooks
        :return: El resultado sumado
        :rtype: app.agent.AgentResult
        """
        deps = self._deps
        model = deps.models[model_name]
        results = []
        for name in chosen:
            agent = Agent(
                model=model,
                directory=None,
                max_steps=deps.settings.agent_max_steps,
                tracer=deps.tracer,
                specialist=deps.specialists[name],
            )
            results.append(
                agent.run(
                    messages,
                    on_tool_call=hooks.tool_call,
                    on_tool_denied=hooks.tool_denied,
                )
            )
        return combine(results)
