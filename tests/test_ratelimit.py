"""Pruebas de ``app.ratelimit.RateLimiter`` con un reloj manual.

La ventana es de 60 segundos por cliente y se mide con ``clock.monotonic()``.
"""

from app.ratelimit import RateLimiter


def test_requests_up_to_the_limit_pass_and_one_more_is_refused(manual_clock):
    limiter = RateLimiter(limit_per_minute=3, clock=manual_clock())

    decisions = [limiter.check("ops").allowed for _ in range(4)]

    assert decisions == [True, True, True, False]


def test_retry_after_counts_the_seconds_until_the_oldest_request_leaves(
    manual_clock,
):
    clock = manual_clock(start=100.0)
    limiter = RateLimiter(limit_per_minute=2, clock=clock)
    limiter.check("ops")
    clock.advance(10)
    limiter.check("ops")

    clock.advance(20)
    refused = limiter.check("ops")
    clock.advance(29.5)
    almost_free = limiter.check("ops")

    # La más vieja (t=100) sale de la ventana en t=160.
    assert (refused.allowed, refused.retry_after) == (False, 30)
    # Medio segundo se redondea hacia arriba: nunca se pide esperar 0.
    assert (almost_free.allowed, almost_free.retry_after) == (False, 1)


def test_refused_requests_do_not_count_and_the_window_frees_after_a_minute(
    manual_clock,
):
    clock = manual_clock(start=100.0)
    limiter = RateLimiter(limit_per_minute=1, clock=clock)

    assert limiter.check("ops").allowed is True
    clock.advance(10)
    assert limiter.check("ops").allowed is False
    clock.advance(10)
    assert limiter.check("ops").allowed is False
    clock.advance(40)

    # A los 60 s de la única petición aceptada, el cliente vuelve a entrar.
    assert limiter.check("ops").allowed is True
