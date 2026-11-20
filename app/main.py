"""API web sin estado del HR Assistant (FastAPI).

Se arranca con ``uvicorn app.main:app`` desde la carpeta ``product/``. La
salida estándar lleva solo el log JSON: el log de acceso de uvicorn se apaga.
Con varios procesos (``--workers``), cada uno arma su propia aplicación con
``create_app``; dentro de un proceso, ``POST /ask`` no comparte estado mutable
entre peticiones (el log, las métricas y el límite usan candados).
"""

import logging
import os
import sys
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, StringConstraints

from app.agent_steps import StepRecorder
from app.alerts import AlertThresholds
from app.audit import AUDIT_TEXT_LIMIT, Auditor, audit_args
from app.budget import BudgetExceeded, ClientBudget
from app.clock import SystemClock
from app.config import load_settings
from app.directory import EmployeeDirectory
from app.guard import Guard, GuardBlocked
from app.http_layers import (
    build_page_csp,
    install_body_limit,
    install_security_headers,
)
from app.llm import ModelUnavailableError, build_models
from app.logs import JsonLogger
from app.metrics import MetricsRecorder
from app.middleware import install_observability
from app.pipeline import (
    CostLevers,
    Forbidden,
    Pipeline,
    PipelineDependencies,
    PipelineResult,
    window_history,
)
from app.policies import PolicyBook
from app.pricing import PriceTable
from app.prometheus import CONTENT_TYPE as PROMETHEUS_CONTENT_TYPE
from app.prometheus import render as render_prometheus
from app.ratelimit import RateLimiter
from app.response_cache import ResponseCache
from app.scopes import PERMISSIONS, permissions_for
from app.security import ApiKeyAuthenticator, install_security
from app.specialists import build_specialists
from app.tracing import Tracer

__all__ = [
    "AUDIT_TEXT_LIMIT",
    "AskResponse",
    "Guardrails",
    "HistoryMessage",
    "app",
    "audit_args",
    "create_app",
    "new_request_id",
    "render_index",
    "window_history",
]

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
DASHBOARD_HTML_PATH = STATIC_DIR / "dashboard.html"
STATIC_FILES = {
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
UVICORN_ACCESS_LOGGER = "uvicorn.access"
MAX_HISTORY_MESSAGES_MARK = "{{MAX_HISTORY_MESSAGES}}"
MAX_HISTORY_CHARS_MARK = "{{MAX_HISTORY_CHARS}}"
MODEL_UNAVAILABLE_STATUS = 502
MODEL_UNAVAILABLE_DETAIL = "model unavailable"
BLOCKED_STATUS = 400
FORBIDDEN_STATUS = 403
FORBIDDEN_DETAIL = "forbidden for this client"
BUDGET_EXCEEDED_STATUS = 429
BUDGET_EXCEEDED_DETAIL = "cost budget exceeded"
RETRY_AFTER_HEADER = "Retry-After"
CACHE_CONTROL_HEADER = "Cache-Control"
NO_STORE = "no-store"
READY_OK = "ok"
# Permisos que existen (``directory``, ``policies`` y ``metrics``).
ALL_PERMISSIONS = PERMISSIONS


class HistoryMessage(BaseModel):
    """Un mensaje previo de la conversación que guarda el navegador.

    :param role: Quién lo escribió: ``user`` o ``assistant``
    :type role: str
    :param content: Texto del mensaje
    :type content: str
    """

    role: Literal["user", "assistant"]
    content: str


class Guardrails(BaseModel):
    """Lo que hicieron el guard y el reviewer con una pregunta.

    :param pii_input: Tipos de dato personal encontrados en la entrada
    :type pii_input: list[str]
    :param pii_output: Tipos de dato personal tapados en la salida
    :type pii_output: list[str]
    :param review: ``passed``, ``redacted`` o ``replaced``
    :type review: str
    """

    pii_input: list[str]
    pii_output: list[str]
    review: Literal["passed", "redacted", "replaced"]


class AskResponse(BaseModel):
    """Respuesta de ``POST /ask``.

    :param answer: Respuesta en español
    :type answer: str
    :param steps: Veces que se llamó al modelo
    :type steps: int
    :param tools_used: Herramientas ejecutadas, en orden
    :type tools_used: list[str]
    :param input_tokens: Tokens de entrada
    :type input_tokens: int
    :param output_tokens: Tokens de salida
    :type output_tokens: int
    :param request_id: Id de la petición, para pasarlo a soporte
    :type request_id: str
    :param model: Modelo que respondió
    :type model: str
    :param route: Motivo del enrutador (por ejemplo ``simple``)
    :type route: str
    :param cached_calls: Llamadas servidas de la caché del gateway
    :type cached_calls: int
    :param cost_usd: Costo de la respuesta en USD, sin redondear
    :type cost_usd: float
    :param agents: Agentes que corrieron, en orden
    :type agents: list[str]
    :param guardrails: Tipos de PII de entrada y de salida y resultado del
        reviewer
    :type guardrails: Guardrails
    :param fallback: ``True`` si respondió ``MODEL_SMALL`` por respaldo
    :type fallback: bool
    :param cache: ``none``, ``gateway`` (alguna llamada vino de la caché del
        gateway) o ``response`` (vino de la caché de respuestas)
    :type cache: str
    """

    answer: str
    steps: int
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    request_id: str
    model: str
    route: str
    cached_calls: int
    cost_usd: float
    agents: list[str]
    guardrails: Guardrails
    fallback: bool
    cache: Literal["none", "gateway", "response"]


def _build_ask_request(settings):
    """Crea el modelo Pydantic del cuerpo de ``POST /ask`` con sus topes.

    Topa el largo de la pregunta, la cantidad de mensajes del historial y el largo
    de cada mensaje; un exceso en cualquiera de ellos responde ``422``.

    :param settings: Configuración con ``max_question_chars``,
        ``max_history_messages`` y ``max_history_chars``
    :type settings: app.config.Settings
    :return: La clase que valida el cuerpo
    :rtype: type[pydantic.BaseModel]
    """
    question_type = Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=settings.max_question_chars,
        ),
    ]
    content_type = Annotated[
        str, StringConstraints(max_length=settings.max_history_chars)
    ]

    class LimitedHistoryMessage(HistoryMessage):
        """Mensaje del historial con el texto topado.

        :param content: Texto del mensaje, de ``max_history_chars`` como máximo
        :type content: str
        """

        content: content_type

    history_type = Annotated[
        list[LimitedHistoryMessage],
        Field(max_length=settings.max_history_messages),
    ]

    class AskRequest(BaseModel):
        """Cuerpo de ``POST /ask``.

        :param question: Pregunta, sin espacios sobrantes
        :type question: str
        :param history: Conversación previa que manda el cliente, con tope de
            ``max_history_messages`` mensajes
        :type history: list[HistoryMessage]
        """

        question: question_type
        history: history_type = []

    return AskRequest


def new_request_id():
    """Genera un id de petición nuevo.

    :return: Un UUID4 como texto
    :rtype: str
    """
    return str(uuid.uuid4())


def _default_tracer(settings):
    """Crea el ``Tracer`` que indica la configuración.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :return: El ``Tracer``, apagado si las trazas no están encendidas
    :rtype: app.tracing.Tracer
    """
    return Tracer(
        enabled=settings.tracing_enabled,
        hide_inputs=settings.hide_inputs,
        hide_outputs=settings.hide_outputs,
        project_name=settings.langsmith_project,
    )


def client_permissions(settings, client):
    """Permisos de un cliente: el único lugar que los decide.

    Lee ``API_KEY_SCOPES`` (punto 3.4 de la spec): en ``development`` un
    cliente sin entrada tiene todos los permisos; en ``production``, ninguno.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param client: Nombre del cliente, o ``None``
    :type client: str or None
    :return: Los permisos del cliente
    :rtype: frozenset[str]
    """
    return permissions_for(client, settings.api_key_scopes, settings.environment)


def _resolve_models(settings, directory, model, models, extra):
    """Elige los modelos por nombre: inyectados o construidos por configuración.

    Si solo se inyecta ``model`` (como en las sesiones 1 y 2), ese modelo sirve
    para los dos nombres.

    :param settings: Configuración con ``model_small`` y ``model_large``
    :type settings: app.config.Settings
    :param directory: Directorio de empleados
    :type directory: app.directory.EmployeeDirectory
    :param model: Modelo único inyectado, o ``None``
    :type model: app.llm.LanguageModel or None
    :param models: Modelos inyectados por nombre, o ``None``
    :type models: dict or None
    :param extra: Cliente HTTP para ``gateway`` (o ``None``) y políticas
    :type extra: tuple
    :return: Los modelos por nombre
    :rtype: dict[str, app.llm.LanguageModel]
    """
    if models is not None:
        return dict(models)
    if model is not None:
        return {settings.model_small: model, settings.model_large: model}
    http_client, policy_book = extra
    return build_models(
        settings, directory, http_client=http_client, policy_book=policy_book
    )


def render_index(template, settings):
    """Llena la página con los topes del historial para que no los pase.

    :param template: HTML de la página con los marcadores de los topes
    :type template: str
    :param settings: Configuración con los topes del historial
    :type settings: app.config.Settings
    :return: El HTML listo para servir
    :rtype: str
    """
    return template.replace(
        MAX_HISTORY_MESSAGES_MARK, str(settings.max_history_messages)
    ).replace(MAX_HISTORY_CHARS_MARK, str(settings.max_history_chars))


def _add_static_routes(application, settings):
    """Agrega la página y sus archivos estáticos (JS y CSS, sin código en línea).

    :param application: Aplicación donde se agregan
    :type application: fastapi.FastAPI
    :param settings: Configuración, de donde salen los topes del historial
    :type settings: app.config.Settings
    """
    index_html = render_index(
        INDEX_HTML_PATH.read_text(encoding="utf-8"), settings
    )
    assets = {
        name: (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in STATIC_FILES
    }

    dashboard_html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")

    @application.get("/", response_class=HTMLResponse)
    def home():
        """Sirve la página web de RH."""
        return HTMLResponse(index_html)

    @application.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        """Sirve el tablero (pública); sus datos piden clave, como en ``/``."""
        return HTMLResponse(dashboard_html)

    @application.get("/static/app.js")
    def script():
        """Sirve el JavaScript de la página."""
        return Response(assets["app.js"], media_type=STATIC_FILES["app.js"])

    @application.get("/static/app.css")
    def stylesheet():
        """Sirve la hoja de estilos de la página."""
        return Response(assets["app.css"], media_type=STATIC_FILES["app.css"])


def _add_read_routes(application, metrics, checks):
    """Agrega las rutas de solo lectura: salud, readiness y métricas.

    :param application: Aplicación donde se agregan
    :type application: fastapi.FastAPI
    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param checks: Resultado de cargar directorio, políticas y precios
    :type checks: dict[str, str]
    """

    @application.get("/health")
    def health():
        """Indica que el servicio está vivo y qué proceso responde."""
        return {"status": "ok", "pid": os.getpid()}

    @application.get("/ready")
    def ready():
        """Indica que los datos se cargaron; no llama al modelo."""
        return {"status": "ready", "checks": dict(checks)}

    @application.get("/metrics")
    def metrics_endpoint():
        """Devuelve las métricas de este proceso, el costo y si se cumple el SLO."""
        return metrics.snapshot(pid=os.getpid())

    @application.get("/metrics/prometheus")
    def prometheus_endpoint():
        """Devuelve las métricas de este proceso en el formato de Prometheus."""
        return Response(
            render_prometheus(metrics.prometheus_counters(), os.getpid()),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )


# Registro plano de dependencias inyectadas: cada una es un campo que se lee por
# nombre, así que pasa del límite de atributos de pylint.
@dataclass(frozen=True)
class AskDependencies:  # pylint: disable=too-many-instance-attributes
    """Dependencias que usa ``POST /ask`` para responder.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param pipeline: Guard, supervisor, especialistas y reviewer
    :type pipeline: app.pipeline.Pipeline
    :param prices: Tabla de precios por modelo
    :type prices: app.pricing.PriceTable
    :param tracer: Trazas de los agentes
    :type tracer: app.tracing.Tracer
    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param auditor: Eventos ``audit``
    :type auditor: app.audit.Auditor
    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param clock: Reloj inyectado (mide cada agente)
    :type clock: app.clock.Clock
    :param budget: Presupuesto diario por cliente (suma cada respuesta
        ``200``); ``None`` no suma nada
    :type budget: app.budget.ClientBudget or None
    """

    settings: object
    pipeline: Pipeline
    prices: PriceTable
    tracer: Tracer
    logger: JsonLogger
    auditor: Auditor
    metrics: MetricsRecorder
    clock: object
    budget: object = None


def _log_agent_run(logger, request_id, payload, run):
    """Escribe el evento ``agent_run`` con conteos, nunca con el texto.

    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param request_id: Id de la petición
    :type request_id: str
    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param run: Resultado del pipeline, costo y cliente
    :type run: dict
    """
    outcome = run["outcome"]
    result = outcome.result
    logger.emit(
        "agent_run",
        request_id,
        client=run["client"],
        question_chars=len(payload.question),
        history_messages=len(payload.history),
        steps=result.steps,
        tools=list(result.tools_used),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        stopped=result.stopped,
        model=outcome.model,
        route=outcome.route.reason,
        history_sent=outcome.history_sent,
        cached_calls=result.cached_calls,
        cost_usd=run["cost_usd"],
        agents=list(outcome.agents),
        fallback=outcome.fallback,
        review=outcome.guardrails["review"],
        cache=outcome.cache,
    )


def _error_response(status, detail, request_id, headers=None):
    """Arma una respuesta de error de ``POST /ask`` con el id de la petición.

    :param status: Código HTTP
    :type status: int
    :param detail: Texto fijo del error (nunca datos de la pregunta)
    :type detail: str
    :param request_id: Id de la petición
    :type request_id: str
    :param headers: Encabezados extra (por ejemplo ``Retry-After``)
    :type headers: dict[str, str] or None
    :return: La respuesta, sin caché
    :rtype: fastapi.responses.JSONResponse
    """
    return JSONResponse(
        status_code=status,
        content={"detail": detail, "request_id": request_id},
        headers={CACHE_CONTROL_HEADER: NO_STORE, **(headers or {})},
    )


def _build_response(outcome, request_id, cost_usd):
    """Arma la ``AskResponse`` a partir del resultado del pipeline.

    :param outcome: Resultado del pipeline
    :type outcome: app.pipeline.PipelineResult
    :param request_id: Id de la petición
    :type request_id: str
    :param cost_usd: Costo de la respuesta en USD
    :type cost_usd: float
    :return: La respuesta de ``POST /ask``
    :rtype: AskResponse
    """
    result = outcome.result
    return AskResponse(
        answer=result.answer,
        steps=result.steps,
        tools_used=list(result.tools_used),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        request_id=request_id,
        model=outcome.model,
        route=outcome.route.reason,
        cached_calls=result.cached_calls,
        cost_usd=cost_usd,
        agents=list(outcome.agents),
        guardrails=Guardrails(**outcome.guardrails),
        fallback=outcome.fallback,
        cache=outcome.cache,
    )


def _answer(deps, payload, request_id, client):
    """Responde una pregunta: pasa por todos los agentes, cobra y registra.

    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param request_id: Id de la petición
    :type request_id: str
    :param client: Nombre del cliente que preguntó (nunca su clave)
    :type client: str or None
    :return: La respuesta, o un ``400``, ``403``, ``429`` o ``502``
    :rtype: AskResponse or fastapi.responses.JSONResponse
    """
    environment = deps.settings.environment
    steps = StepRecorder(deps.clock, deps.tracer, environment)
    with deps.tracer.run_context(request_id, client=client):
        with deps.tracer.request_span(request_id, client, environment) as root:
            try:
                outcome = _run_pipeline(deps, payload, request_id, client, steps)
            finally:
                _record_steps(deps, steps, request_id, client)
                root.annotate(agents=steps.ran())
            if not isinstance(outcome, PipelineResult):
                return outcome
            result = outcome.result
            cost_usd = deps.prices.cost_usd(
                outcome.model, result.billed_input_tokens, result.billed_output_tokens
            )
            root.annotate(
                model=outcome.model,
                route=outcome.route.reason,
                agents=list(outcome.agents),
                cost_usd=cost_usd,
                cache=outcome.cache,
                fallback=outcome.fallback,
            )
    _log_agent_run(
        deps.logger,
        request_id,
        payload,
        {"outcome": outcome, "cost_usd": cost_usd, "client": client},
    )
    _record_answer(deps.metrics, outcome, cost_usd, client)
    if deps.budget is not None:
        deps.budget.add(client, cost_usd)
    return _build_response(outcome, request_id, cost_usd)


def _run_pipeline(deps, payload, request_id, client, steps):
    """Pasa la pregunta por los agentes y traduce sus errores a respuestas.

    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param request_id: Id de la petición
    :type request_id: str
    :param client: Nombre del cliente que preguntó (nunca su clave)
    :type client: str or None
    :param steps: Pasos medidos de la petición
    :type steps: app.agent_steps.StepRecorder
    :return: El resultado del pipeline, o un ``400``, ``403``, ``429``
        (presupuesto, con ``Retry-After``) o ``502``
    :rtype: app.pipeline.PipelineResult or fastapi.responses.JSONResponse
    """
    history = [message.model_dump() for message in payload.history]
    try:
        return deps.pipeline.answer(
            payload.question,
            history,
            client_permissions(deps.settings, client),
            deps.auditor.bind(request_id, client),
            steps=steps,
            client=client,
        )
    except GuardBlocked as blocked:
        return _error_response(BLOCKED_STATUS, blocked.detail, request_id)
    except Forbidden:
        return _error_response(FORBIDDEN_STATUS, FORBIDDEN_DETAIL, request_id)
    except BudgetExceeded as exceeded:
        return _error_response(
            BUDGET_EXCEEDED_STATUS,
            BUDGET_EXCEEDED_DETAIL,
            request_id,
            headers={RETRY_AFTER_HEADER: str(exceeded.retry_after)},
        )
    except ModelUnavailableError as error:
        # Al log solo va el tipo de error: el mensaje no debe llegar al log.
        deps.logger.emit("error", request_id, error_type=type(error).__name__)
        return _error_response(
            MODEL_UNAVAILABLE_STATUS, MODEL_UNAVAILABLE_DETAIL, request_id
        )


def _record_steps(deps, steps, request_id, client):
    """Escribe un ``agent_step`` por agente y lo suma a las métricas.

    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    :param steps: Pasos medidos de la petición
    :type steps: app.agent_steps.StepRecorder
    :param request_id: Id de la petición
    :type request_id: str
    :param client: Nombre del cliente, o ``None``
    :type client: str or None
    """
    for step in steps.steps:
        deps.logger.emit(
            "agent_step",
            request_id,
            client=client,
            agent=step.agent,
            latency_ms=step.latency_ms,
            outcome=step.outcome,
        )
        deps.metrics.record_agent(step.agent, step.outcome, step.latency_ms)


def _record_answer(metrics, outcome, cost_usd, client):
    """Suma una respuesta ``200`` a las métricas técnicas y de negocio.

    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param outcome: Resultado del pipeline
    :type outcome: app.pipeline.PipelineResult
    :param cost_usd: Costo de la respuesta en USD
    :type cost_usd: float
    :param client: Nombre del cliente, o ``None``
    :type client: str or None
    """
    result = outcome.result
    metrics.add_tokens(
        input_tokens=result.input_tokens, output_tokens=result.output_tokens
    )
    metrics.add_cost(cost_usd)
    metrics.record_answer(
        client=client,
        model=outcome.model,
        route=outcome.route.reason,
        cost_usd=cost_usd,
        calls=result.steps,
        cache=outcome.cache,
        fallback=outcome.fallback,
    )


def _add_ask_route(application, deps):
    """Agrega la ruta ``POST /ask``, que no guarda nada en el servidor.

    :param application: Aplicación donde se agrega
    :type application: fastapi.FastAPI
    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    """
    ask_request = _build_ask_request(deps.settings)

    @application.post("/ask", response_model=AskResponse)
    def ask(payload: ask_request, request: Request, response: Response):
        """Responde una pregunta con los agentes; no guarda nada en el servidor."""
        response.headers[CACHE_CONTROL_HEADER] = NO_STORE
        return _answer(
            deps,
            payload,
            request.state.request_id,
            getattr(request.state, "client", None),
        )


def _build_pipeline(settings, models, data, tracer, levers):
    """Arma el pipeline de agentes.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param models: Modelos por nombre
    :type models: dict[str, app.llm.LanguageModel]
    :param data: Directorio de empleados y políticas de RH
    :type data: tuple[app.directory.EmployeeDirectory, app.policies.PolicyBook]
    :param tracer: Trazas de los agentes
    :type tracer: app.tracing.Tracer
    :param levers: Caché de respuestas y presupuesto por cliente
    :type levers: app.pipeline.CostLevers
    :return: El pipeline
    :rtype: app.pipeline.Pipeline
    """
    directory, policy_book = data
    return Pipeline(
        PipelineDependencies(
            settings=settings,
            guard=Guard(settings),
            specialists=build_specialists(directory, policy_book),
            departments=list(directory.departments()),
            policy_book=policy_book,
            models=models,
            tracer=tracer,
        ),
        levers=levers,
    )


def _build_metrics(settings):
    """Crea las métricas del proceso con los umbrales de las alertas.

    :param settings: Configuración con ``SLO_P95_MS`` y ``ALERT_*``
    :type settings: app.config.Settings
    :return: Las métricas
    :rtype: app.metrics.MetricsRecorder
    """
    return MetricsRecorder(
        slo_p95_ms=settings.slo_p95_ms,
        thresholds=AlertThresholds(
            slo_p95_ms=settings.slo_p95_ms,
            error_rate=settings.alert_error_rate,
            cost_per_request_usd=settings.alert_cost_per_request_usd,
            guardrail_events=settings.alert_guardrail_events,
        ),
    )


# Cada dependencia se inyecta por separado para que las pruebas controlen hora,
# log, trazas e ids; por eso la firma pasa del límite de argumentos de pylint.
def create_app(  # pylint: disable=too-many-arguments,too-many-locals
    settings=None,
    model=None,
    directory=None,
    *,
    clock=None,
    log_stream=None,
    tracer=None,
    new_id=None,
    models=None,
    http_client=None,
):
    """Arma la aplicación FastAPI con sus dependencias.

    Las dependencias que no se pasan se construyen desde el entorno. Cada una se
    puede inyectar para que las pruebas sean deterministas.

    :param settings: Configuración; por omisión se lee de ``os.environ``
    :type settings: app.config.Settings or None
    :param model: Modelo de lenguaje; por omisión el que indica la configuración
    :type model: app.llm.LanguageModel or None
    :param directory: Directorio de empleados; por omisión el de la configuración
    :type directory: app.directory.EmployeeDirectory or None
    :param clock: Reloj; por omisión el del sistema
    :type clock: app.clock.Clock or None
    :param log_stream: Destino del log JSON; por omisión la salida estándar
    :type log_stream: typing.TextIO or None
    :param tracer: Trazas; por omisión las que indica la configuración
    :type tracer: app.tracing.Tracer or None
    :param new_id: Generador de ids de petición; por omisión UUID4
    :type new_id: collections.abc.Callable[[], str] or None
    :param models: Modelos por nombre (``MODEL_SMALL`` y ``MODEL_LARGE``); por
        omisión ``model`` para los dos, o los que indica la configuración
    :type models: dict[str, app.llm.LanguageModel] or None
    :param http_client: Cliente HTTP del modelo ``gateway``; por omisión uno
        nuevo
    :type http_client: httpx.Client or None
    :return: La aplicación lista para servir
    :rtype: fastapi.FastAPI
    :raises app.config.ConfigError: Si la configuración no es válida
    """
    settings = settings if settings is not None else load_settings(os.environ)
    if directory is None:
        directory = EmployeeDirectory(settings.employees_path)
    clock = clock or SystemClock()
    tracer = tracer or _default_tracer(settings)
    policy_book = PolicyBook.load(settings.policies_path)
    models = _resolve_models(
        settings, directory, model, models, (http_client, policy_book)
    )
    # Palancas de costo del reto (parte 2): las dos son del proceso.
    budget = ClientBudget(daily_usd=settings.client_daily_budget_usd, clock=clock)
    response_cache = ResponseCache(
        ttl_seconds=settings.response_cache_ttl_seconds,
        max_entries=settings.response_cache_max_entries,
        clock=clock,
    )
    pipeline = _build_pipeline(
        settings,
        models,
        (directory, policy_book),
        tracer,
        CostLevers(response_cache=response_cache, budget=budget),
    )
    logger = JsonLogger(
        stream=log_stream or sys.stdout,
        clock=clock,
        service=settings.service_name,
    )
    metrics = _build_metrics(settings)
    deps = AskDependencies(
        settings=settings,
        pipeline=pipeline,
        prices=PriceTable.load(settings.prices_path),
        tracer=tracer,
        logger=logger,
        # ``guardrails`` sale de la misma fuente que el audit.
        auditor=Auditor(logger, observer=metrics.observe_audit),
        metrics=metrics,
        clock=clock,
        budget=budget,
    )
    # Si algo no carga, ``create_app`` falla antes; aquí todo está listo.
    checks = {"directory": READY_OK, "policies": READY_OK, "prices": READY_OK}

    application = FastAPI(title="HR Assistant")
    # Starlette pone por fuera el último middleware agregado. Orden de
    # instalación: tamaño, seguridad, observabilidad y encabezados. Así la clave
    # (401) y el límite (429) van antes del tamaño (413), el tamaño antes de
    # validar (422), y toda respuesta lleva ``X-Request-ID`` y los encabezados.
    install_body_limit(application, settings.max_body_bytes)
    install_security(
        application,
        ApiKeyAuthenticator(settings.api_keys),
        RateLimiter(settings.rate_limit_per_minute, clock),
        auditor=deps.auditor,
        permissions=partial(client_permissions, settings),
    )
    install_observability(
        application, deps.logger, deps.metrics, clock, new_id or new_request_id
    )
    install_security_headers(
        application,
        settings.environment,
        page_csp=build_page_csp(DASHBOARD_HTML_PATH.read_text(encoding="utf-8")),
    )
    _add_static_routes(application, settings)
    _add_read_routes(application, deps.metrics, checks)
    _add_ask_route(application, deps)
    return application


def _silence_uvicorn_access_log():
    """Apaga el log de acceso de uvicorn para que la salida sea solo JSON."""
    logging.getLogger(UVICORN_ACCESS_LOGGER).disabled = True


_silence_uvicorn_access_log()
app = create_app()
