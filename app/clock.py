"""Reloj inyectable: el único lugar del producto que lee la hora real."""

import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Interfaz del reloj que reciben el log y el middleware."""

    def now_utc(self):
        """Devuelve la hora actual con zona UTC.

        :return: La hora actual
        :rtype: datetime.datetime
        """

    def monotonic(self):
        """Devuelve un contador en segundos que nunca decrece.

        :return: Segundos de un contador monotónico
        :rtype: float
        """


class SystemClock:
    """Reloj real del sistema."""

    def now_utc(self):
        """Devuelve la hora actual del sistema con zona UTC.

        :return: La hora actual
        :rtype: datetime.datetime
        """
        return datetime.now(timezone.utc)

    def monotonic(self):
        """Devuelve el contador monotónico del sistema.

        :return: Segundos de un contador que nunca decrece
        :rtype: float
        """
        return time.monotonic()
