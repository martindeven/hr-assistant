"""Pruebas de la tabla de precios: USD por millón de tokens."""

import json

import pytest

from app.pricing import PriceTable

PRICES = {
    "currency": "USD",
    "unit": "per million tokens",
    "models": {
        "fake": {"input": 0.0, "output": 0.0},
        "small": {"input": 0.5, "output": 2.0},
    },
}


@pytest.fixture
def prices_file(tmp_path):
    """Un archivo de precios con números fáciles de comprobar."""
    path = tmp_path / "model_prices.json"
    path.write_text(json.dumps(PRICES), encoding="utf-8")
    return path


def test_cost_uses_the_price_per_million_tokens(prices_file):
    table = PriceTable.load(prices_file)

    # (2_000_000 * 0.5 + 1_000 * 2.0) / 1_000_000 = 1.002
    assert table.cost_usd("small", 2_000_000, 1_000) == pytest.approx(1.002)
    assert table.cost_usd("fake", 1_000, 1_000) == 0


def test_unknown_model_and_invalid_price_file_are_rejected(prices_file, tmp_path):
    table = PriceTable.load(prices_file)
    broken = tmp_path / "broken.json"
    broken.write_text("esto no es JSON", encoding="utf-8")

    with pytest.raises(ValueError):
        table.cost_usd("@cf/unknown/model", 10, 10)
    with pytest.raises(ValueError):
        PriceTable.load(broken)
