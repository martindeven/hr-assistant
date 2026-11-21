# Evidencia: prueba de carga contra Render

- **Servicio:** `https://hr-assistant-ar3g.onrender.com` (rama `reto/produccion`, plan Free).
- **Fecha:** 2026-11-20.
- **Cliente:** `hr-portal`. La clave se tomó de `LOAD_API_KEY` y no aparece aquí.
- **Modelo:** real (Workers AI vía AI Gateway).
- **Límite:** `RATE_LIMIT_PER_MINUTE=30`.

## Comando

```bash
uv run python scripts/load_test.py https://hr-assistant-ar3g.onrender.com --requests 200 --concurrency 20
```

## Resultados

| Métrica | Corrida 1: 1 proceso | Corrida 2: 2 procesos (`WEB_CONCURRENCY=2`) |
| --- | --- | --- |
| Peticiones totales | 200 | 200 |
| Concurrencia | 20 | 20 |
| Respuestas `200` | 30 | 59 |
| Respuestas `429` (límite por cliente) | 170 | 141 |
| Respuestas `5xx` | 0 | 0 |
| Latencia p50 | 192 ms | 264 ms |
| Latencia p95 | 2579 ms | 3815 ms |
| Latencia máxima | 3362 ms | 4768 ms |
| Procesos distintos en `/health` | 1 (`pid` 1) | 2 (`pid` 25 y 27) |

## Lectura

- **Corrida 1.** Render no tenía `WEB_CONCURRENCY`, así que uvicorn arrancó un solo proceso (`pid` 1). Se
  corrigió agregando `WEB_CONCURRENCY=2` en Render > Environment.
- **Corrida 2: escalado horizontal.** Respondieron 2 procesos distintos.
- **El límite es por proceso.** Con un proceso pasaron 30 preguntas; con dos pasaron 59, casi 2 x 30. Coincide con
  la decisión documentada en el README y el ADR 0015: el límite y las métricas viven en cada proceso, y compartirlos
  entre procesos necesitaría Redis.
- **Protección contra abuso.** El exceso se frenó con `429`, sin ningún `5xx`. Es el control
  contra el consumo sin límite de OWASP LLM10.
- **Latencia.** Incluye el modelo real. El p95 sube en la corrida 2 porque llegan al modelo el doble de preguntas
  al mismo tiempo; la latencia de los `429` es mínima, porque no llegan al modelo.
