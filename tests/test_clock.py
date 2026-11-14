"""Pruebas de ``app.clock.SystemClock``, el único lector de la hora real."""

from datetime import timedelta

from app.clock import SystemClock


def test_system_clock_gives_utc_time_and_a_counter_that_never_goes_back():
    clock = SystemClock()

    assert clock.now_utc().utcoffset() == timedelta(0)
    first = clock.monotonic()
    second = clock.monotonic()
    assert second >= first
