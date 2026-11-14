"""API web sin estado del HR Assistant (FastAPI).

Se arranca con ``uvicorn app.main:app`` desde la carpeta ``product/``. La
salida estándar lleva solo el log JSON: el log de acceso de uvicorn se apaga.
"""

import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from app.agent import Agent
from app.clock import SystemClock
from app.config import load_settings
from app.directory import EmployeeDirectory
from app.llm import ModelUnavailableError, build_models
from app.logs import JsonLogger
from app.metrics import MetricsRecorder
from app.middleware import install_observability
from app.pricing import PriceTable
from app.ratelimit import RateLimiter
from app.router import choose_route
from app.security import ApiKeyAuthenticator, install_security
from app.tracing import Tracer

INDEX_HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"
AUDIT_TEXT_LIMIT = 200
UVICORN_ACCESS_LOGGER = "uvicorn.access"
MAX_HISTORY_MESSAGES_MARK = "{{MAX_HISTORY_MESSAGES}}"
MAX_HISTORY_CHARS_MARK = "{{MAX_HISTORY_CHARS}}"
MODEL_UNAVAILABLE_STATUS = 502
MODEL_UNAVAILABLE_DETAIL = "model unavailable"


class HistoryMessage(BaseModel):
    """Un mensaje previo de la conversación que guarda el navegador.

    :param role: Quién lo escribió: ``user`` o ``assistant``
    :type role: str
    :param content: Texto del mensaje
    :type content: str
    """

    role: Literal["user", "assistant"]
    content: str


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
    :param model: Modelo que eligió el enrutador
    :type model: str
    :param route: Motivo del enrutador (por ejemplo ``simple``)
    :type route: str
    :param cached_calls: Llamadas servidas de la caché del gateway
    :type cached_calls: int
    :param cost_usd: Costo de la respuesta en USD, sin redondear
    :type cost_usd: float
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


def _trim(value):
    """Recorta un texto al largo máximo del audit; otros valores quedan igual.

    :param value: Valor a recortar
    :type value: object
    :return: El valor, con los textos recortados
    :rtype: object
    """
    if isinstance(value, str):
        return value[:AUDIT_TEXT_LIMIT]
    return value


def audit_args(args):
    """Prepara los argumentos de una herramienta para el evento ``audit``.

    Se audita lo que pidió el modelo, aunque no sea válido, con cada texto
    recortado a ``AUDIT_TEXT_LIMIT`` caracteres.

    :param args: Argumentos que pidió el modelo
    :type args: object
    :return: Los argumentos con los textos recortados
    :rtype: object
    """
    if isinstance(args, dict):
        return {_trim(str(key)): _trim(value) for key, value in args.items()}
    return _trim(args)


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


def _resolve_models(settings, directory, model, models, http_client):
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
    :param http_client: Cliente HTTP para ``gateway``, o ``None``
    :type http_client: httpx.Client or None
    :return: Los modelos por nombre
    :rtype: dict[str, app.llm.LanguageModel]
    """
    if models is not None:
        return dict(models)
    if model is not None:
        return {settings.model_small: model, settings.model_large: model}
    return build_models(settings, directory, http_client=http_client)


def _log_agent_run(logger, request_id, payload, run):
    """Escribe el evento ``agent_run`` con conteos, nunca con el texto.

    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param request_id: Id de la petición
    :type request_id: str
    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param run: Resultado del agente, ruta, mensajes enviados, costo y cliente
    :type run: dict
    """
    result = run["result"]
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
        model=run["route"].model,
        route=run["route"].reason,
        history_sent=run["history_sent"],
        cached_calls=result.cached_calls,
        cost_usd=run["cost_usd"],
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


def _add_read_routes(application, metrics, settings):
    """Agrega las rutas de solo lectura: página, salud y métricas.

    :param application: Aplicación donde se agregan
    :type application: fastapi.FastAPI
    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param settings: Configuración, de donde salen los topes del historial
    :type settings: app.config.Settings
    """
    index_html = render_index(
        INDEX_HTML_PATH.read_text(encoding="utf-8"), settings
    )

    @application.get("/", response_class=HTMLResponse)
    def home():
        """Sirve la página web de RH."""
        return HTMLResponse(index_html)

    @application.get("/health")
    def health():
        """Indica que el servicio está vivo y qué proceso responde."""
        return {"status": "ok", "pid": os.getpid()}

    @application.get("/metrics")
    def metrics_endpoint():
        """Devuelve las métricas de este proceso, el costo y si se cumple el SLO."""
        return metrics.snapshot(pid=os.getpid())


@dataclass(frozen=True)
class AskDependencies:
    """Dependencias que usa ``POST /ask`` para responder.

    :param settings: Configuración de la aplicación
    :type settings: app.config.Settings
    :param agent: Agente que responde las preguntas
    :type agent: app.agent.Agent
    :param models: Modelos por nombre
    :type models: dict[str, app.llm.LanguageModel]
    :param prices: Tabla de precios por modelo
    :type prices: app.pricing.PriceTable
    :param tracer: Trazas del agente
    :type tracer: app.tracing.Tracer
    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    """

    settings: object
    agent: Agent
    models: dict
    prices: PriceTable
    tracer: Tracer
    logger: JsonLogger
    metrics: MetricsRecorder


def _build_messages(payload, settings):
    """Arma los mensajes para el modelo: historial recortado y la pregunta.

    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param settings: Configuración con ``history_window``
    :type settings: app.config.Settings
    :return: Los mensajes y cuántos del historial se mandaron
    :rtype: tuple[list[dict], int]
    """
    history = window_history(payload.history, settings.history_window)
    messages = [message.model_dump() for message in history]
    messages.append({"role": "user", "content": payload.question})
    return messages, len(history)


def _audit_callback(logger, request_id, client):
    """Crea el callback que escribe un evento ``audit`` por herramienta.

    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param request_id: Id de la petición
    :type request_id: str
    :param client: Nombre del cliente que preguntó (nunca su clave)
    :type client: str or None
    :return: Función ``(tool, args)`` que escribe el evento
    :rtype: collections.abc.Callable[[str, object], None]
    """

    def audit(tool, args):
        """Escribe un evento ``audit`` por cada herramienta pedida."""
        logger.emit(
            "audit",
            request_id,
            client=client,
            action="tool_call",
            tool=tool,
            args=audit_args(args),
        )

    return audit


def _model_unavailable_response(logger, request_id, error):
    """Registra la falla del modelo y arma la respuesta ``502``.

    Al log solo va el tipo de error: el mensaje no debe llegar al log.

    :param logger: Log JSON de eventos
    :type logger: app.logs.JsonLogger
    :param request_id: Id de la petición
    :type request_id: str
    :param error: Error que levantó el modelo
    :type error: app.llm.ModelUnavailableError
    :return: La respuesta ``502`` con el id de la petición
    :rtype: fastapi.responses.JSONResponse
    """
    logger.emit("error", request_id, error_type=type(error).__name__)
    return JSONResponse(
        status_code=MODEL_UNAVAILABLE_STATUS,
        content={"detail": MODEL_UNAVAILABLE_DETAIL, "request_id": request_id},
    )


def _record_usage(metrics, result, cost_usd):
    """Suma los tokens y el costo de una respuesta a las métricas.

    :param metrics: Métricas del proceso
    :type metrics: app.metrics.MetricsRecorder
    :param result: Resultado del agente
    :type result: app.agent.AgentResult
    :param cost_usd: Costo de la respuesta en USD
    :type cost_usd: float
    """
    metrics.add_tokens(
        input_tokens=result.input_tokens, output_tokens=result.output_tokens
    )
    metrics.add_cost(cost_usd)


def _build_response(result, route, request_id, cost_usd):
    """Arma la ``AskResponse`` a partir del resultado del agente.

    :param result: Resultado del agente
    :type result: app.agent.AgentResult
    :param route: Ruta que eligió el enrutador
    :type route: app.router.Route
    :param request_id: Id de la petición
    :type request_id: str
    :param cost_usd: Costo de la respuesta en USD
    :type cost_usd: float
    :return: La respuesta de ``POST /ask``
    :rtype: AskResponse
    """
    return AskResponse(
        answer=result.answer,
        steps=result.steps,
        tools_used=list(result.tools_used),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        request_id=request_id,
        model=route.model,
        route=route.reason,
        cached_calls=result.cached_calls,
        cost_usd=cost_usd,
    )


def _answer(deps, payload, request_id, client):
    """Responde una pregunta: enruta, corre el agente, cobra y registra.

    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    :param payload: Cuerpo validado de ``POST /ask``
    :type payload: pydantic.BaseModel
    :param request_id: Id de la petición
    :type request_id: str
    :param client: Nombre del cliente que preguntó (nunca su clave)
    :type client: str or None
    :return: La respuesta, o un ``502`` si el modelo no está disponible
    :rtype: AskResponse or fastapi.responses.JSONResponse
    """
    messages, history_sent = _build_messages(payload, deps.settings)
    route = choose_route(payload.question, deps.settings)
    try:
        with deps.tracer.run_context(request_id, client=client):
            result = deps.agent.run(
                messages,
                on_tool_call=_audit_callback(deps.logger, request_id, client),
                model=deps.models[route.model],
            )
    except ModelUnavailableError as error:
        return _model_unavailable_response(deps.logger, request_id, error)
    cost_usd = deps.prices.cost_usd(
        route.model, result.billed_input_tokens, result.billed_output_tokens
    )
    _log_agent_run(
        deps.logger,
        request_id,
        payload,
        {
            "result": result,
            "route": route,
            "history_sent": history_sent,
            "cost_usd": cost_usd,
            "client": client,
        },
    )
    _record_usage(deps.metrics, result, cost_usd)
    return _build_response(result, route, request_id, cost_usd)


def _add_ask_route(application, deps):
    """Agrega la ruta ``POST /ask``, que no guarda nada en el servidor.

    :param application: Aplicación donde se agrega
    :type application: fastapi.FastAPI
    :param deps: Dependencias de ``POST /ask``
    :type deps: AskDependencies
    """
    ask_request = _build_ask_request(deps.settings)

    @application.post("/ask", response_model=AskResponse)
    def ask(payload: ask_request, request: Request):
        """Responde una pregunta con el agente; no guarda nada en el servidor."""
        return _answer(
            deps,
            payload,
            request.state.request_id,
            getattr(request.state, "client", None),
        )


# Cada dependencia se inyecta por separado para que las pruebas controlen hora,
# log, trazas e ids; por eso la firma pasa del límite de argumentos de pylint.
def create_app(  # pylint: disable=too-many-arguments
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
    models = _resolve_models(settings, directory, model, models, http_client)
    clock = clock or SystemClock()
    tracer = tracer or _default_tracer(settings)
    deps = AskDependencies(
        settings=settings,
        agent=Agent(
            model=models[settings.model_large],
            directory=directory,
            max_steps=settings.agent_max_steps,
            tracer=tracer,
        ),
        models=models,
        prices=PriceTable.load(settings.prices_path),
        tracer=tracer,
        logger=JsonLogger(
            stream=log_stream or sys.stdout,
            clock=clock,
            service=settings.service_name,
        ),
        metrics=MetricsRecorder(slo_p95_ms=settings.slo_p95_ms),
    )

    application = FastAPI(title="HR Assistant")
    # Starlette pone por fuera el último middleware agregado: la seguridad va
    # primero para que la observabilidad envuelva también los 401 y 429.
    install_security(
        application,
        ApiKeyAuthenticator(settings.api_keys),
        RateLimiter(settings.rate_limit_per_minute, clock),
    )
    install_observability(
        application, deps.logger, deps.metrics, clock, new_id or new_request_id
    )
    _add_read_routes(application, deps.metrics, settings)
    _add_ask_route(application, deps)
    return application


def _silence_uvicorn_access_log():
    """Apaga el log de acceso de uvicorn para que la salida sea solo JSON."""
    logging.getLogger(UVICORN_ACCESS_LOGGER).disabled = True


_silence_uvicorn_access_log()
app = create_app()
