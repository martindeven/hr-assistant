"""Límite de peticiones por cliente con ventana deslizante de 60 segundos.

El estado vive en memoria del proceso: con varios procesos el límite real se
multiplica (ver ADR 0010). El reloj se inyecta para que las pruebas decidan
cuándo pasa un minuto.
"""

import math
import threading
from collections import deque
from dataclasses import dataclass

WINDOW_SECONDS = 60
MIN_RETRY_AFTER_SECONDS = 1


@dataclass(frozen=True)
class Decision:
    """Resultado de revisar el límite de un cliente.

    :param allowed: Si la petición puede seguir
    :type allowed: bool
    :param retry_after: Segundos que faltan para que se libere un lugar (0 si
        la petición pasa)
    :type retry_after: int
    """

    allowed: bool
    retry_after: int = 0


class RateLimiter:
    """Cuenta las peticiones aceptadas de cada cliente en el último minuto.

    Solo cuentan las peticiones aceptadas: una rechazada no ocupa lugar. Cada
    cliente guarda a lo sumo ``limit_per_minute`` instantes, así que la memoria
    queda acotada por la cantidad de clientes configurados.

    :param limit_per_minute: Peticiones permitidas por cliente en 60 segundos
    :type limit_per_minute: int
    :param clock: Reloj inyectado; se usa ``clock.monotonic()``
    :type clock: app.clock.Clock
    :raises ValueError: Si el límite no es un entero positivo
    """

    def __init__(self, limit_per_minute, clock):
        if limit_per_minute < 1:
            raise ValueError("limit_per_minute must be a positive integer")
        self._limit = limit_per_minute
        self._clock = clock
        self._windows = {}
        self._lock = threading.Lock()

    def check(self, client):
        """Revisa el límite del cliente y, si pasa, registra la petición.

        :param client: Nombre del cliente (nunca su clave)
        :type client: str
        :return: Si la petición pasa y, si no, cuántos segundos esperar
        :rtype: Decision
        """
        with self._lock:
            now = self._clock.monotonic()
            window = self._windows.setdefault(client, deque(maxlen=self._limit))
            while window and now - window[0] >= WINDOW_SECONDS:
                window.popleft()
            if len(window) >= self._limit:
                remaining = math.ceil(window[0] + WINDOW_SECONDS - now)
                return Decision(
                    allowed=False,
                    retry_after=max(MIN_RETRY_AFTER_SECONDS, remaining),
                )
            window.append(now)
            return Decision(allowed=True)
