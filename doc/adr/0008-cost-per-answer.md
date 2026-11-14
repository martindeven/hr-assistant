# 0008. Costo por respuesta con precios en archivo

Estado: aceptado

## Contexto

Cada palanca de costo debe medirse, y debe poder medirse sin red ni cuota. Los precios del
proveedor cambian y no deben quedar en el código.

## Decisión

- `pricing.py` lee `data/model_prices.json` (USD por millón de tokens) y calcula
  `(entrada * precio_entrada + salida * precio_salida) / 1_000_000`.
- El precio se busca por el **nombre** del modelo elegido, también con el modelo simulado.
- Las llamadas con `cf-aig-cache-status: HIT` cuentan en `cached_calls` y no suman costo; los
  tokens totales sí se siguen reportando.
- `POST /ask` responde `model`, `route`, `cached_calls` y `cost_usd` sin redondear; `/metrics`
  suma `cost_usd` del proceso (como los tokens, ADR 0005).
- Si `MODEL_SMALL` o `MODEL_LARGE` no tienen precio, la app no arranca.

## Consecuencias

- `evaluate` compara costo y calidad con el modelo simulado, gratis y repetible.
- Con el simulado los tokens son palabras: el costo sirve para comparar palancas, no para
  presupuestar.
- Actualizar precios es editar un archivo; hay que revisarlo contra la página del proveedor.
