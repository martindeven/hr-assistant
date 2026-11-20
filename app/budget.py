"""Presupuesto diario por cliente (reto, parte 2, punto 7).

Suma el costo de las respuestas de cada cliente en el día UTC del reloj
inyectado. Cuando el gasto del día alcanza ``CLIENT_DAILY_BUDGET_USD``, el
cliente espera hasta la medianoche UTC. Con tope 0 no hay límite.

El presupuesto es del proceso, igual que el límite de peticiones: con varios
procesos el tope efectivo se multiplica. Dos preguntas simultáneas pueden
pasar el tope por una respuesta; se acepta.
"""

import math
import threading
from datetime import datetime, time, timedelta, timezone

# Se reutilizan del límite de peticiones: la decisión tiene la misma forma
# (``allowed`` y ``retry_after``, aquí los segundos hasta la medianoche UTC).
from app.ratelimit import MIN_RETRY_AFTER_SECONDS, Decision

__all__ = [
    "MIN_RETRY_AFTER_SECONDS",
    "BudgetExceeded",
    "ClientBudget",
    "Decision",
    "seconds_until_utc_midnight",
]


class BudgetExceeded(Exception):
    """El cliente gastó su presupuesto del día.

    :param retry_after: Segundos hasta la medianoche UTC
    :type retry_after: int
    """

    def __init__(self, retry_after):
        super().__init__("cost budget exceeded")
        self.retry_after = retry_after


def _as_utc(moment):
    """Pasa una hora a UTC; si no trae zona, se toma como UTC.

    :param moment: Hora del reloj
    :type moment: datetime.datetime
    :return: La hora con zona UTC
    :rtype: datetime.datetime
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def seconds_until_utc_midnight(moment):
    """Segundos que faltan para la siguiente medianoche UTC.

    :param moment: Hora actual
    :type moment: datetime.datetime
    :return: Los segundos, redondeados hacia arriba y al menos 1
    :rtype: int
    """
    now = _as_utc(moment)
    midnight = datetime.combine(
        now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    remaining = math.ceil((midnight - now).total_seconds())
    return max(MIN_RETRY_AFTER_SECONDS, remaining)


class ClientBudget:
    """Presupuesto diario de costo por cliente.

    :param daily_usd: Tope diario en USD (0: sin tope)
    :type daily_usd: float
    :param clock: Reloj inyectado (se usa ``now_utc``)
    :type clock: app.clock.Clock
    """

    def __init__(self, daily_usd, clock):
        self.daily_usd = daily_usd
        self._clock = clock
        # Por cliente: el día UTC y lo gastado ese día.
        self._spent = {}
        self._lock = threading.Lock()

    @property
    def enabled(self):
        """Indica si hay tope.

        :return: ``True`` si ``daily_usd`` es mayor que 0
        :rtype: bool
        """
        return self.daily_usd > 0

    def _today(self):
        """Hora y fecha UTC actuales según el reloj inyectado.

        :return: La hora y su fecha
        :rtype: tuple[datetime.datetime, datetime.date]
        """
        now = _as_utc(self._clock.now_utc())
        return now, now.date()

    def check(self, client):
        """Revisa si el cliente puede preguntar.

        :param client: Nombre del cliente
        :type client: str or None
        :return: La decisión; si no puede, con los segundos hasta la
            medianoche UTC
        :rtype: Decision
        """
        if not self.enabled:
            return Decision(allowed=True)
        now, today = self._today()
        with self._lock:
            day, spent = self._spent.get(client, (today, 0.0))
        if day != today or spent < self.daily_usd:
            return Decision(allowed=True)
        return Decision(allowed=False, retry_after=seconds_until_utc_midnight(now))

    def add(self, client, cost_usd):
        """Suma el costo de una respuesta al día UTC del cliente.

        :param client: Nombre del cliente
        :type client: str or None
        :param cost_usd: Costo de la respuesta en USD
        :type cost_usd: float
        """
        if not self.enabled:
            return
        _, today = self._today()
        with self._lock:
            day, spent = self._spent.get(client, (today, 0.0))
            if day != today:
                spent = 0.0
            self._spent[client] = (today, spent + cost_usd)
