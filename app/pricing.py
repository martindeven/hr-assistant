"""Precios por modelo y costo de cada respuesta, en USD.

Los precios viven en ``data/model_prices.json`` (USD por millón de tokens), no
en el código: cambian con el tiempo y hay que revisarlos.
"""

import json
from pathlib import Path

TOKENS_PER_PRICE_UNIT = 1_000_000


class PriceError(ValueError):
    """El archivo de precios no es válido o un modelo no tiene precio."""


def _parse_price(name, raw):
    """Valida el precio de un modelo.

    :param name: Nombre del modelo
    :type name: str
    :param raw: Objeto con ``input`` y ``output``
    :type raw: object
    :return: Los precios de entrada y salida por millón de tokens
    :rtype: tuple[float, float]
    :raises PriceError: Si faltan los precios o no son números de 0 en adelante
    """
    if not isinstance(raw, dict):
        raise PriceError(f"price of {name!r} must be an object")
    prices = []
    for kind in ("input", "output"):
        value = raw.get(kind)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PriceError(f"price {kind!r} of {name!r} must be a number")
        if value < 0:
            raise PriceError(f"price {kind!r} of {name!r} must not be negative")
        prices.append(float(value))
    return prices[0], prices[1]


class PriceTable:
    """Tabla de precios por nombre de modelo.

    :param prices: Precios ``(entrada, salida)`` por millón de tokens, por modelo
    :type prices: dict[str, tuple[float, float]]
    """

    def __init__(self, prices):
        self._prices = dict(prices)

    @classmethod
    def load(cls, path):
        """Lee la tabla de un archivo JSON con la clave ``models``.

        :param path: Ruta del archivo de precios
        :type path: str or pathlib.Path
        :return: La tabla de precios
        :rtype: PriceTable
        :raises PriceError: Si el archivo no se puede leer o no es válido
        """
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PriceError(f"cannot read the price file {str(path)!r}") from error
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, dict):
            raise PriceError("the price file must have a 'models' object")
        return cls(
            {str(name): _parse_price(name, raw) for name, raw in models.items()}
        )

    def has_price(self, model):
        """Indica si un modelo tiene precio.

        :param model: Nombre del modelo
        :type model: str
        :return: ``True`` si está en la tabla
        :rtype: bool
        """
        return model in self._prices

    def cost_usd(self, model, input_tokens, output_tokens):
        """Calcula el costo de una cantidad de tokens, sin redondear.

        :param model: Nombre del modelo
        :type model: str
        :param input_tokens: Tokens de entrada facturables
        :type input_tokens: int
        :param output_tokens: Tokens de salida facturables
        :type output_tokens: int
        :return: El costo en USD
        :rtype: float
        :raises PriceError: Si el modelo no tiene precio
        """
        if model not in self._prices:
            raise PriceError(f"model {model!r} has no price")
        input_price, output_price = self._prices[model]
        total = input_tokens * input_price + output_tokens * output_price
        return total / TOKENS_PER_PRICE_UNIT
