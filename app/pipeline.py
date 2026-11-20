"""Orquestación de una pregunta: guard, supervisor, especialistas y reviewer.

El pipeline no guarda nada entre peticiones: cada llamada a ``answer`` arma su
propio estado, así que se puede usar desde varios hilos a la vez.
"""

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from app.agent import Agent, AgentResult
from app.audit import audit_args
from app.budget import BudgetExceeded
from app.guard import GUARD_AGENT
from app.llm import HELP_ANSWER, SYSTEM_PROMPTS, ModelUnavailableError
from app.response_cache import CachedAnswer, cache_key
from app.reviewer import REVIEW_REPLACED, REVIEWER_AGENT, review
from app.router import choose_route
from app.supervisor import plan, restrict

SUPERVISOR_AGENT = "supervisor"
ANSWER_SEPARATOR = "\n"
EVIDENCE_ID_PATTERN = re.compile(r"EMP-\d{4}", re.IGNORECASE)
FIXED_TEXTS = (HELP_ANSWER,)
CACHE_NONE = "none"
CACHE_GATEWAY = "gateway"
CACHE_RESPONSE = "response"


class Forbidden(Exception):
    """El cliente no tiene permiso para ninguno de los especialistas elegidos."""


@dataclass(frozen=True)
class CostLevers:
    """Palancas de costo del reto (parte 2) que revisa el pipeline.

    :param response_cache: Caché de respuestas; ``None`` la apaga
    :type response_cache: app.response_cache.ResponseCache or None
    :param budget: Presupuesto diario por cliente; ``None`` lo apaga
    :type budget: app.budget.ClientBudget or None
    """

    response_cache: object = None
    budget: object = None


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
    :param cache: ``none``, ``gateway`` (alguna llamada vino de la caché del
        gateway) o ``response`` (lo agrega ``costs``)
    :type cache: str
    """

    result: AgentResult
    route: object
    model: str
    agents: list = field(default_factory=list)
    guardrails: dict = field(default_factory=dict)
    fallback: bool = False
    history_sent: int = 0
    cache: str = CACHE_NONE


class _NullSteps:
    """Pasos que no miden nada, para usar el pipeline sin ``StepRecorder``."""

    @staticmethod
    def step(agent):
        """Contexto vacío para un agente.

        :param agent: Nombre del agente
        :type agent: str
        :return: Un administrador de contexto que no hace nada
        :rtype: contextlib.AbstractContextManager
        """
        del agent
        return contextlib.nullcontext()

    @staticmethod
    def skip(agent):
        """No registra nada.

        :param agent: Nombre del agente
        :type agent: str
        """
        del agent


def _skip_all(steps, agents):
    """Marca como ``skipped`` los agentes que ya no van a correr.

    :param steps: Pasos medidos de la petición
    :type steps: app.agent_steps.StepRecorder
    :param agents: Nombres de los agentes, en orden
    :type agents: collections.abc.Iterable[str]
    """
    for agent in agents:
        steps.skip(agent)


def _from_cache(hit, checked, history_sent):
    """Arma el resultado de un acierto de la caché de respuestas.

    No llama al modelo: cuesta 0, sin pasos, herramientas ni tokens. Conserva
    el modelo, la ruta y la revisión guardados; ``pii_input`` es el de esta
    pregunta.

    :param hit: Respuesta guardada
    :type hit: app.response_cache.CachedAnswer
    :param checked: Entrada revisada por el guard
    :type checked: app.guard.GuardResult
    :param history_sent: Mensajes del historial que entran en la llave
    :type history_sent: int
    :return: El resultado listo para la respuesta
    :rtype: PipelineResult
    """
    return PipelineResult(
        result=AgentResult(answer=hit.answer, steps=0),
        route=hit.route,
        model=hit.model,
        agents=[GUARD_AGENT, SUPERVISOR_AGENT],
        guardrails={
            "pii_input": list(checked.pii_types),
            "pii_output": list(hit.pii_output),
            "review": hit.review,
        },
        fallback=False,
        history_sent=history_sent,
        cache=CACHE_RESPONSE,
    )


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
    :param steps: Pasos medidos de la petición
    :type steps: object
    """

    audit: Callable
    steps: object = None

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
    :param levers: Caché de respuestas y presupuesto; por omisión apagados
    :type levers: CostLevers or None
    """

    def __init__(self, deps, *, levers=None):
        self._deps = deps
        self._levers = levers or CostLevers()

    # La firma es el contrato entre ``observability`` y ``costs`` (plan): los
    # argumentos nuevos van por nombre para no romper a quien ya la usa.
    def answer(  # pylint: disable=too-many-arguments
        self, question, history, permissions, audit, *, steps=None, client=None
    ):
        """Responde una pregunta.

        Cada agente corre dentro de ``steps.step(nombre)``; los que ya no
        corren tras un bloqueo o un error quedan ``skipped``. Entre el
        supervisor y los especialistas se revisan el presupuesto del cliente
        y la caché de respuestas: un acierto no llama al modelo y deja a los
        especialistas y al reviewer ``skipped``.

        :param question: Pregunta validada
        :type question: str
        :param history: Historial validado, con ``role`` y ``content``
        :type history: list[dict]
        :param permissions: Permisos del cliente
        :type permissions: set[str]
        :param audit: Función ``(action, outcome, **campos)`` de la petición
        :type audit: collections.abc.Callable
        :param steps: Pasos medidos de la petición; por omisión no se miden
        :type steps: app.agent_steps.StepRecorder or None
        :param client: Nombre del cliente (lo usan presupuesto y caché)
        :type client: str or None
        :return: El resultado listo para la respuesta
        :rtype: PipelineResult
        :raises app.guard.GuardBlocked: Si el guard bloquea la petición
        :raises Forbidden: Si el cliente no puede usar ningún especialista elegido
        :raises app.budget.BudgetExceeded: Si el cliente gastó su presupuesto
            del día
        :raises app.llm.ModelUnavailableError: Si ningún modelo responde
        """
        steps = steps or _NullSteps()
        try:
            with steps.step(GUARD_AGENT):
                checked = self._deps.guard.check(question, history, audit=audit)
        except Exception:
            _skip_all(steps, [SUPERVISOR_AGENT, REVIEWER_AGENT])
            raise
        route = choose_route(checked.question, self._deps.settings)
        chosen = self._supervise(checked.question, permissions, audit, steps)
        messages = self._messages(checked)
        self._check_budget(client, audit, steps, chosen)
        key = self._cache_key(checked.question, permissions, messages)
        hit = self._lookup(key)
        if hit is not None:
            _skip_all(steps, [*chosen, REVIEWER_AGENT])
            return _from_cache(hit, checked, history_sent=len(messages) - 1)
        outcome = self._specialists_answer(
            (checked, route, chosen), messages, _Hooks(audit, steps)
        )
        self._store(key, outcome)
        return outcome

    def _specialists_answer(self, planned, messages, hooks):
        """Corre los especialistas y el reviewer, y arma el resultado.

        :param planned: Entrada revisada por el guard, ruta del enrutador y
            especialistas permitidos
        :type planned: tuple
        :param messages: Historial recortado y pregunta
        :type messages: list[dict]
        :param hooks: Auditoría y pasos de la petición
        :type hooks: _Hooks
        :return: El resultado listo para la respuesta
        :rtype: PipelineResult
        :raises app.llm.ModelUnavailableError: Si ningún modelo responde
        """
        checked, route, chosen = planned
        audit, steps = hooks.audit, hooks.steps
        try:
            result, model, fallback = self._run_with_fallback(
                chosen, messages, route, hooks
            )
        except Exception:
            steps.skip(REVIEWER_AGENT)
            raise
        reviewed = self._review(checked.question, result, audit, steps)
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
            # Todos los mensajes menos la pregunta vienen del historial.
            history_sent=len(messages) - 1,
            cache=CACHE_GATEWAY if result.cached_calls > 0 else CACHE_NONE,
        )

    def _check_budget(self, client, audit, steps, chosen):
        """Revisa el presupuesto del día del cliente antes de llamar al modelo.

        :param client: Nombre del cliente, o ``None``
        :type client: str or None
        :param audit: Función ``(action, outcome, **campos)`` de la petición
        :type audit: collections.abc.Callable
        :param steps: Pasos medidos de la petición
        :type steps: app.agent_steps.StepRecorder
        :param chosen: Especialistas permitidos, en orden
        :type chosen: list[str]
        :raises app.budget.BudgetExceeded: Si el cliente ya alcanzó su tope
        """
        budget = self._levers.budget
        if budget is None:
            return
        decision = budget.check(client)
        if decision.allowed:
            return
        _skip_all(steps, [*chosen, REVIEWER_AGENT])
        audit("budget_exceeded", "denied", retry_after=decision.retry_after)
        raise BudgetExceeded(decision.retry_after)

    def _cache_key(self, question, permissions, messages):
        """Calcula la llave de la caché de respuestas, si está encendida.

        :param question: Pregunta revisada por el guard
        :type question: str
        :param permissions: Permisos del cliente
        :type permissions: collections.abc.Iterable[str]
        :param messages: Historial recortado y pregunta (la última)
        :type messages: list[dict]
        :return: La llave, o ``None`` con la caché apagada
        :rtype: str or None
        """
        cache = self._levers.response_cache
        if cache is None or not cache.enabled:
            return None
        allowed = set(permissions) & set(self._deps.specialists)
        return cache_key(question, allowed, messages[:-1])

    def _lookup(self, key):
        """Busca una respuesta en la caché.

        :param key: Llave de la respuesta, o ``None`` con la caché apagada
        :type key: str or None
        :return: La respuesta guardada, o ``None``
        :rtype: app.response_cache.CachedAnswer or None
        """
        if key is None:
            return None
        return self._levers.response_cache.get(key)

    def _store(self, key, outcome):
        """Guarda una respuesta en la caché si se puede reutilizar.

        No se guardan respuestas por tope de pasos, reemplazadas por el
        reviewer ni de respaldo (los errores nunca llegan aquí).

        :param key: Llave de la respuesta, o ``None`` con la caché apagada
        :type key: str or None
        :param outcome: Resultado de la pregunta
        :type outcome: PipelineResult
        """
        reusable = not (
            outcome.fallback
            or outcome.result.stopped
            or outcome.guardrails["review"] == REVIEW_REPLACED
        )
        if key is None or not reusable:
            return
        self._levers.response_cache.put(
            key,
            CachedAnswer(
                answer=outcome.result.answer,
                model=outcome.model,
                route=outcome.route,
                pii_output=tuple(outcome.guardrails["pii_output"]),
                review=outcome.guardrails["review"],
            ),
        )

    def _messages(self, checked):
        """Arma los mensajes para el modelo: historial recortado y pregunta.

        :param checked: Entrada revisada por el guard
        :type checked: app.guard.GuardResult
        :return: El historial que se manda (``HISTORY_WINDOW``) y al final la
            pregunta
        :rtype: list[dict]
        """
        sent_history = window_history(
            checked.history, self._deps.settings.history_window
        )
        messages = [dict(message) for message in sent_history]
        messages.append({"role": "user", "content": checked.question})
        return messages

    def _review(self, question, result, audit, steps):
        """Corre el reviewer sobre la respuesta sumada de los especialistas.

        :param question: Pregunta revisada por el guard
        :type question: str
        :param result: Resultado sumado de los especialistas
        :type result: app.agent.AgentResult
        :param audit: Función ``(action, outcome, **campos)`` de la petición
        :type audit: collections.abc.Callable
        :param steps: Pasos medidos de la petición
        :type steps: app.agent_steps.StepRecorder
        :return: La respuesta revisada
        :rtype: app.reviewer.ReviewResult
        """
        with steps.step(REVIEWER_AGENT):
            return review(
                result.answer,
                _evidence_ids(question, result),
                SYSTEM_PROMPTS.values(),
                FIXED_TEXTS,
                max_chars=self._deps.settings.max_answer_chars,
                audit=audit,
            )

    def _supervise(self, question, permissions, audit, steps):
        """Corre el supervisor: elige especialistas y los filtra por permisos.

        :param question: Pregunta revisada por el guard
        :type question: str
        :param permissions: Permisos del cliente
        :type permissions: set[str]
        :param audit: Función ``(action, outcome, **campos)`` de la petición
        :type audit: collections.abc.Callable
        :param steps: Pasos medidos de la petición
        :type steps: app.agent_steps.StepRecorder
        :return: Los especialistas permitidos, en orden
        :rtype: list[str]
        :raises Forbidden: Si no queda ningún especialista
        """
        deps = self._deps
        try:
            with steps.step(SUPERVISOR_AGENT):
                chosen = restrict(
                    plan(question, deps.departments, deps.policy_book), permissions
                )
                if not chosen:
                    audit("forbidden", "denied")
                    raise Forbidden()
        except Exception:
            steps.skip(REVIEWER_AGENT)
            raise
        return chosen

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
        steps = hooks.steps or _NullSteps()
        results = []
        for index, name in enumerate(chosen):
            agent = Agent(
                model=model,
                directory=None,
                max_steps=deps.settings.agent_max_steps,
                tracer=deps.tracer,
                specialist=deps.specialists[name],
                trace_loop=False,
            )
            try:
                with steps.step(name):
                    results.append(
                        agent.run(
                            messages,
                            on_tool_call=hooks.tool_call,
                            on_tool_denied=hooks.tool_denied,
                        )
                    )
            except Exception:
                _skip_all(steps, chosen[index + 1 :])
                raise
        return combine(results)
