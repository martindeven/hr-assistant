"""Caché de respuestas y presupuesto por cliente (reto, parte 2, puntos 6 y 7).

Los relojes son de prueba: la caché vence y el día cambia sin esperar.
"""

from datetime import datetime, timezone

from app.budget import ClientBudget
from app.response_cache import ResponseCache, cache_key

QUESTION = "¿Quién es EMP-0004?"
BOTH = ["directory", "policies"]
BEFORE_MIDNIGHT = datetime(2026, 1, 15, 23, 59, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 1, 16, 0, 0, 1, tzinfo=timezone.utc)


def test_only_case_accents_and_spaces_can_change_without_changing_the_cache_key():
    key = cache_key(QUESTION, BOTH, [])

    assert cache_key("¿QUIEN   es emp-0004?", ["policies", "directory"], []) == key
    assert cache_key(QUESTION, ["policies"], []) != key
    history = [{"role": "user", "content": "Hola"}]
    assert cache_key(QUESTION, BOTH, history) != key


def test_a_cached_answer_expires_after_its_ttl(manual_clock):
    clock = manual_clock()
    cache = ResponseCache(ttl_seconds=300, max_entries=10, clock=clock)

    cache.put("key", "answer")
    clock.advance(299)
    kept = cache.get("key")
    clock.advance(2)

    assert kept == "answer"
    assert cache.get("key") is None
    assert cache.get("never-stored") is None


def test_a_full_cache_drops_the_least_recently_used_answer(manual_clock):
    cache = ResponseCache(ttl_seconds=300, max_entries=2, clock=manual_clock())

    cache.put("first", "1")
    cache.put("second", "2")
    cache.get("first")
    cache.put("third", "3")

    assert cache.get("second") is None
    assert (cache.get("first"), cache.get("third")) == ("1", "3")


def test_a_zero_budget_never_blocks_a_client(utc_clock):
    budget = ClientBudget(daily_usd=0, clock=utc_clock(BEFORE_MIDNIGHT))

    budget.add("portal", 1000.0)

    assert budget.check("portal").allowed is True


def test_each_client_has_its_own_budget_until_utc_midnight(utc_clock):
    clock = utc_clock(BEFORE_MIDNIGHT)
    budget = ClientBudget(daily_usd=0.01, clock=clock)

    budget.add("portal", 0.005)
    under = budget.check("portal")
    budget.add("portal", 0.01)
    over = budget.check("portal")
    other = budget.check("kiosk")
    clock.now = NEXT_DAY

    assert under.allowed is True
    assert (over.allowed, over.retry_after) == (False, 60)
    assert other.allowed is True
    assert budget.check("portal").allowed is True
