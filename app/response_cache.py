"""Caché de respuestas (reto, parte 2, punto 6).

Guarda respuestas ya revisadas para no volver a llamar al modelo con una
pregunta repetida. La llave lleva la pregunta normalizada, los especialistas
permitidos al cliente y un hash del historial que se manda al modelo, así que
dos clientes con permisos distintos nunca comparten una respuesta.

La caché es del proceso: con varios procesos cada uno tiene la suya. Usa un
candado porque ``POST /ask`` corre en varios hilos.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from app.text import fold

HASH_ENCODING = "utf-8"


def _digest(value):
    """Calcula el sha256 de un valor serializable como JSON.

    :param value: Valor a resumir
    :type value: object
    :return: El hash como texto hexadecimal
    :rtype: str
    """
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode(HASH_ENCODING)).hexdigest()


def cache_key(question, allowed, history):
    """Calcula la llave de una respuesta en la caché.

    La pregunta se normaliza con :func:`app.text.fold` (sin mayúsculas, sin
    acentos y con los espacios juntos). Los especialistas permitidos se ordenan
    y se quitan repetidos, así que su orden no cambia la llave.

    :param question: Pregunta ya revisada por el guard
    :type question: str
    :param allowed: Especialistas permitidos al cliente
    :type allowed: collections.abc.Iterable[str]
    :param history: Historial que se manda al modelo (ya recortado y tapado)
    :type history: list[dict]
    :return: La llave como texto hexadecimal
    :rtype: str
    """
    return _digest([fold(question), sorted(set(allowed)), _digest(list(history))])


@dataclass(frozen=True)
class CachedAnswer:
    """Lo que se guarda de una respuesta para servirla otra vez.

    :param answer: Respuesta revisada
    :type answer: str
    :param model: Modelo que la respondió
    :type model: str
    :param route: Ruta que eligió el enrutador
    :type route: app.router.Route
    :param pii_output: Tipos de dato personal tapados en la salida
    :type pii_output: tuple[str, ...]
    :param review: Resultado del reviewer (``passed`` o ``redacted``)
    :type review: str
    """

    answer: str
    model: str
    route: object
    pii_output: tuple = ()
    review: str = "passed"


class ResponseCache:
    """Caché de respuestas con vencimiento y tope de entradas (LRU).

    Con ``ttl_seconds`` igual a 0 está apagada: no guarda ni acierta. El
    vencimiento usa ``clock.monotonic()``; cuando se llena, sale la entrada que
    se usó hace más tiempo.

    :param ttl_seconds: Segundos que vive una respuesta (0: apagada)
    :type ttl_seconds: int
    :param max_entries: Entradas máximas
    :type max_entries: int
    :param clock: Reloj inyectado
    :type clock: app.clock.Clock
    """

    def __init__(self, ttl_seconds, max_entries, clock):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self):
        """Indica si la caché está encendida.

        :return: ``True`` si ``ttl_seconds`` es mayor que 0
        :rtype: bool
        """
        return self.ttl_seconds > 0 and self.max_entries > 0

    def get(self, key):
        """Busca una respuesta guardada que no haya vencido.

        Un acierto la marca como la más usada; una vencida se borra.

        :param key: Llave de la respuesta
        :type key: str
        :return: La respuesta guardada, o ``None``
        :rtype: object or None
        """
        if not self.enabled:
            return None
        now = self._clock.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key, value):
        """Guarda una respuesta; si la caché se llena, saca la menos usada.

        :param key: Llave de la respuesta
        :type key: str
        :param value: Respuesta a guardar
        :type value: object
        """
        if not self.enabled:
            return
        expires_at = self._clock.monotonic() + self.ttl_seconds
        with self._lock:
            self._entries[key] = (expires_at, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
