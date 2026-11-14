# 0005. Métricas en memoria, por proceso y con ventana fija

Estado: aceptado

## Contexto

`GET /metrics` debe dar peticiones, tasa de error, latencia p50/p95, tokens y si se cumple el SLO
(`SLO_P95_MS`). La API es sin estado (ADR 0001) y escala corriendo más copias. No hay librerías de
métricas en el entorno.

## Decisión

- `MetricsRecorder` guarda en memoria del proceso: total de peticiones, las 5xx, tokens de
  entrada y salida, y las últimas 1000 latencias en una `deque`. Usa un candado.
- Percentiles por rango más cercano sobre esa ventana; sin datos valen 0. `met` es
  `p95 < SLO_P95_MS`. No se cuenta la propia ruta `/metrics`.
- La respuesta incluye `pid` para saber qué copia responde.

## Consecuencias

- No contradice el ADR 0001: no es estado de usuario y no cambia ninguna respuesta de `/ask`.
- Cada copia reporta solo lo suyo y se pierde al reiniciar; para una vista global hará falta
  agregar fuera (por ejemplo, un recolector) en otra sesión.
- La memoria está acotada por la ventana.
