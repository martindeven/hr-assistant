"""Fixtures compartidas: configuración, directorio, modelos y cliente de la API.

Todas las dependencias se inyectan en ``create_app`` para que las pruebas no
dependan de las variables de entorno de quien las corre.
"""

import io
import itertools
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.directory import EmployeeDirectory
from app.llm import Completion, FakeModel
from app.main import create_app
from app.tracing import Tracer

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
# Clave de prueba de la sesión 4: no es un secreto real. Las pruebas de las
# sesiones 1 a 3 la mandan por omisión y usan un límite alto para no toparlo.
TEST_CLIENT = "unit-tests"
# Se arma en tiempo de ejecución con palabras repetidas para que no parezca una
# clave real ni la marque gitleaks.
TEST_API_KEY = "-".join(["unit", "test"] * 3)
API_KEY_HEADER = "X-API-Key"
KEYED_ENV = {
    "API_KEYS": f"{TEST_CLIENT}:{TEST_API_KEY}",
    "RATE_LIMIT_PER_MINUTE": "1000",
}


def with_test_key(env=None):
    """Agrega la clave de prueba y un límite alto a un entorno.

    Las variables de ``env`` ganan, para que una prueba pueda cambiar las claves
    o el límite.

    :param env: Variables propias de la prueba
    :type env: dict or None
    :return: El entorno completo
    :rtype: dict
    """
    return {**KEYED_ENV, **(env or {})}


class ManualClock:
    """Reloj de prueba que solo avanza cuando la prueba lo pide.

    Sirve para el límite de peticiones: varias peticiones caen en el mismo
    instante y la prueba decide cuándo pasa un minuto.
    """

    def __init__(self, start=100.0):
        self._value = start

    def now_utc(self):
        """Devuelve siempre la misma hora en UTC.

        :return: La hora fija de las pruebas
        :rtype: datetime.datetime
        """
        return FIXED_NOW

    def monotonic(self):
        """Devuelve el contador actual sin avanzarlo.

        :return: Segundos de un contador que nunca decrece
        :rtype: float
        """
        return self._value

    def advance(self, seconds):
        """Avanza el contador.

        :param seconds: Segundos que pasan
        :type seconds: float
        """
        self._value += seconds


class FakeClock:
    """Reloj de prueba: hora fija y un contador monotónico que avanza solo.

    Cada llamada a ``monotonic`` avanza ``step_seconds``; así la latencia
    medida es conocida y ninguna prueba usa ``sleep``.

    :param step_seconds: Segundos que avanza el contador en cada lectura
    :type step_seconds: float
    """

    def __init__(self, step_seconds=0.01):
        self._step = step_seconds
        self._value = 100.0

    def now_utc(self):
        """Devuelve siempre la misma hora en UTC.

        :return: La hora fija de las pruebas
        :rtype: datetime.datetime
        """
        return FIXED_NOW

    def monotonic(self):
        """Devuelve el contador actual y lo avanza un paso.

        :return: Segundos de un contador que nunca decrece
        :rtype: float
        """
        value = self._value
        self._value += self._step
        return value


class UtcClock:
    """Reloj de prueba con la hora UTC que fija la prueba (reto, parte 2).

    Sirve para el presupuesto diario: la prueba pone la hora cerca de la
    medianoche y después la mueve al día siguiente. El contador monotónico no
    avanza solo.

    :param now: Hora UTC inicial
    :type now: datetime.datetime
    """

    def __init__(self, now):
        self.now = now
        self._value = 100.0

    def now_utc(self):
        """Devuelve la hora UTC que fijó la prueba.

        :return: La hora actual del reloj
        :rtype: datetime.datetime
        """
        return self.now

    def monotonic(self):
        """Devuelve el contador sin avanzarlo.

        :return: Segundos de un contador que nunca decrece
        :rtype: float
        """
        return self._value

    def advance(self, seconds):
        """Avanza el contador monotónico.

        :param seconds: Segundos que pasan
        :type seconds: float
        """
        self._value += seconds


class ExplodingModel:
    """Modelo de prueba que falla con un mensaje que trae datos personales."""

    SECRET_MESSAGE = "salary of EMP-0004 is 99999"

    def complete(self, messages):
        """Lanza siempre ``RuntimeError``.

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :raises RuntimeError: Siempre
        """
        raise RuntimeError(self.SECRET_MESSAGE)


def sequential_ids():
    """Crea un generador de ids deterministas con forma de UUID.

    :return: Función sin argumentos que devuelve un UUID nuevo como texto
    :rtype: collections.abc.Callable[[], str]
    """
    counter = itertools.count(1)
    return lambda: str(uuid.UUID(int=next(counter)))


@dataclass
class ObservedClient:
    """Cliente de la API junto con el log que escribe.

    :param http: Cliente de pruebas de FastAPI
    :type http: fastapi.testclient.TestClient
    :param log_stream: Destino en memoria del log JSON
    :type log_stream: io.StringIO
    """

    http: TestClient
    log_stream: io.StringIO

    def events(self):
        """Lee el log como una lista de eventos.

        :return: Un diccionario por línea del log
        :rtype: list[dict]
        """
        return [json.loads(line) for line in self.log_stream.getvalue().splitlines()]


class ScriptedModel:
    """Modelo de prueba que devuelve respuestas guionadas, en orden.

    Guarda una copia de los mensajes de cada llamada para revisarlos después.

    :param replies: Contenidos que devolverá, uno por llamada
    :type replies: list[str]
    """

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def complete(self, messages):
        """Devuelve la siguiente respuesta guionada (10 tokens de entrada, 2 de salida).

        :param messages: Conversación que recibe el modelo
        :type messages: list[dict]
        :return: La respuesta guionada
        :rtype: Completion
        """
        self.calls.append([dict(message) for message in messages])
        return Completion(
            content=self._replies.pop(0), input_tokens=10, output_tokens=2
        )


@pytest.fixture
def settings():
    """Configuración por omisión, sin leer el entorno real."""
    return load_settings({})


@pytest.fixture
def directory(settings):
    """El directorio de empleados de ``product/data/employees.json``."""
    return EmployeeDirectory(settings.employees_path)


@pytest.fixture
def scripted_model():
    """La clase del modelo guionado, para crear uno por prueba."""
    return ScriptedModel


@pytest.fixture
def make_client(directory):
    """Fabrica un ``TestClient`` con el modelo simulado y el entorno indicado."""

    def _make(env=None):
        app = create_app(
            settings=load_settings(with_test_key(env)),
            model=FakeModel(directory.departments()),
            directory=directory,
        )
        return TestClient(app, headers={API_KEY_HEADER: TEST_API_KEY})

    return _make


@pytest.fixture
def fake_clock():
    """La clase del reloj falso, para crear uno por prueba."""
    return FakeClock


@pytest.fixture
def make_observed_client(directory):
    """Fabrica un cliente con reloj falso, log en memoria e ids fijos.

    El ``Tracer`` va apagado: ninguna prueba toca la red ni la hora real. Con
    ``send_key=False`` el cliente no manda ``X-API-Key`` por omisión. Con
    ``models`` se inyecta un modelo por nombre (reto, parte 1: respaldo) y con
    ``directory`` otro directorio (por ejemplo, uno que anota sus lecturas).
    """

    # Cada dependencia es opcional para que cada prueba cambie solo la suya.
    def _make(  # pylint: disable=too-many-arguments
        model=None, clock=None, env=None, send_key=True, models=None, **overrides
    ):
        log_stream = io.StringIO()
        app = create_app(
            settings=load_settings(with_test_key(env)),
            model=model or FakeModel(directory.departments()),
            directory=overrides.get("directory", directory),
            clock=clock or FakeClock(),
            log_stream=log_stream,
            tracer=Tracer(enabled=False),
            new_id=sequential_ids(),
            models=models,
        )
        headers = {API_KEY_HEADER: TEST_API_KEY} if send_key else {}
        return ObservedClient(
            http=TestClient(app, headers=headers), log_stream=log_stream
        )

    return _make


@pytest.fixture
def manual_clock():
    """La clase del reloj manual, para crear uno por prueba."""
    return ManualClock


PROMETHEUS_SAMPLE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?\s+(\S+)$")
PROMETHEUS_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse_prometheus(text):
    """Lee las muestras de un texto en formato de Prometheus 0.0.4.

    Las líneas ``#`` (``HELP`` y ``TYPE``) y las vacías se saltan.

    :param text: Cuerpo de ``GET /metrics/prometheus``
    :type text: str
    :return: Una tupla ``(nombre, etiquetas, valor)`` por muestra
    :rtype: list[tuple[str, dict[str, str], float]]
    :raises AssertionError: Si una línea no tiene la forma de una muestra
    """
    samples = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        found = PROMETHEUS_SAMPLE.match(line)
        assert found, f"not a Prometheus sample: {line!r}"
        name, labels, value = found.groups()
        samples.append(
            (name, dict(PROMETHEUS_LABEL.findall(labels or "")), float(value))
        )
    return samples


@pytest.fixture
def prometheus_samples():
    """La función que lee las muestras del texto de Prometheus."""
    return parse_prometheus


@pytest.fixture
def utc_clock():
    """La clase del reloj con hora UTC fija, para crear uno por prueba."""
    return UtcClock


@pytest.fixture
def keyed_env():
    """La función que agrega la clave de prueba a un entorno."""
    return with_test_key


@pytest.fixture
def api_key():
    """La clave de prueba que mandan los clientes por omisión."""
    return TEST_API_KEY


@pytest.fixture
def exploding_model():
    """Un modelo que siempre lanza una excepción con datos personales."""
    return ExplodingModel()
