# Observabilidad del HR Assistant

Este documento explica cómo seguir una pregunta de punta a punta, qué hacer cuando se enciende
una alerta y cómo armar el tablero de LangSmith. Lo que mide cada campo está en el `README.md`
(«Observabilidad» y «Cómo leer el log»).

Todo es **del proceso**: con `WEB_CONCURRENCY=2` cada proceso tiene su log, sus métricas, sus
alertas y su tablero. El campo `pid` dice qué proceso respondió; Prometheus suma por `pid`.

## Seguir un `request_id`

1. **Navegador.** La página muestra «Id de la petición: ...» debajo de cada respuesta (también
   en los errores). Es el mismo valor que el encabezado `X-Request-ID` de la respuesta. Si el
   cliente manda su propio `X-Request-ID` válido, se usa ese.
2. **Log.** Busca el id en la salida estándar (en Render, en Logs):
   - `audit`: lo que revisaron el guard, el supervisor y el reviewer y cada herramienta pedida.
   - `agent_step`: un evento por agente (`guard`, `supervisor`, `directory` o `policies`,
     `reviewer`) con su `latency_ms` y su `outcome`. El primer agente que no dice `ok` explica
     el resultado: `blocked` (guard, `400`), `forbidden` (supervisor, `403`), `error` (el modelo
     no respondió; si después hay otro paso `ok` del mismo especialista, respondió el modelo de
     respaldo) y `skipped` (no llegó a correr).
   - `agent_run`: el resumen de la respuesta `200` (`model`, `route`, `agents`, `cost_usd`,
     `cache`, `fallback`, `review`).
   - `request`: siempre el último, con el código HTTP, la latencia total y el `client`.
   - `alert`: si esa petición encendió o apagó una alerta.
3. **`/metrics` y `/dashboard`.** Las métricas no guardan ids (serían demasiadas etiquetas): la
   petición aparece sumada en `requests`, `by_agent`, `by_client`, `by_model`, `by_route`,
   `guardrails` y `cache`. Usa el `pid` del evento `request` para saber qué proceso la contó.
4. **LangSmith.** En el proyecto `LANGSMITH_PROJECT`, filtra por el metadato
   `request_id = <id>`. La traza tiene el nodo raíz `ask` y un hijo por agente; dentro de cada
   especialista hay un nodo `llm` por llamada al modelo y un nodo `tool` por herramienta. Con
   `HIDE_INPUTS` y `HIDE_OUTPUTS` en `true` no se ven la pregunta ni la respuesta: solo ids,
   conteos, nombres, latencias y costo.

## Runbook de las alertas

Las alertas se calculan en cada petición (salvo `/metrics` y `/metrics/prometheus`) sobre la
ventana de las últimas 1000 peticiones del proceso. El evento `alert` sale solo cuando una
alerta cambia de estado; con pocas muestras una alerta puede encenderse y apagarse seguido: mira
la tendencia en el tablero antes de actuar. Las alertas encendidas salen en `alerts` de
`/metrics` y en el tablero.

| Alerta | Qué dice | Qué hacer |
| --- | --- | --- |
| `p95_above_slo` | El p95 de latencia pasó `SLO_P95_MS` | Revisa `by_agent.p95_ms` para ver qué agente tarda. Si son los especialistas, revisa el estado de Workers AI y los reintentos (`MODEL_RETRIES`, `MODEL_TIMEOUT_SECONDS`); si el tráfico subió, sube `WEB_CONCURRENCY` o las instancias. |
| `error_rate_high` | Más de `ALERT_ERROR_RATE` de las respuestas son `5xx` | Busca eventos `error` en el log (`error_type`). `502` es el modelo: revisa el gateway, el token y los respaldos (`fallbacks`). `500` es un error del producto: sigue el `request_id` y abre un incidente. |
| `cost_per_request_high` | El costo promedio de las respuestas `200` pasó `ALERT_COST_PER_REQUEST_USD` | Revisa `by_route` y `by_model`: si muchas preguntas van al modelo grande, revisa `ROUTER_MAX_CHARS` y las palabras clave; revisa `HISTORY_WINDOW`, `MODEL_MAX_TOKENS` y la tasa de caché. Compara los precios de `data/model_prices.json`. |
| `guardrail_spike` | Más de `ALERT_GUARDRAIL_EVENTS` bloqueos (inyección, PII en modo `block` o permisos) en la ventana | Revisa `guardrails` y los `audit` (`injection_blocked`, `forbidden`, `pii_detected`) para ver qué `client` los causa. Si es un ataque, quita o rota su clave en `API_KEYS`; si es un cliente mal configurado, corrige su `API_KEY_SCOPES`. |

Para apagar una alerta no hace falta reiniciar: se apaga sola cuando el valor vuelve a quedar en
su umbral o debajo. Reiniciar el proceso borra la ventana y también la evidencia.

## Tablero en LangSmith

Con las trazas encendidas, arma un tablero en el proyecto `LANGSMITH_PROJECT`
(Monitoring > Dashboards > New dashboard):

1. **Latencia por agente.** Gráfica de latencia (p50 y p95) de las corridas con `run type =
   chain`, agrupada por el metadato `agent`. Sin agrupar, filtrada por `name = ask`, da la
   latencia total.
2. **Errores por agente.** Gráfica de tasa de error agrupada por `agent`; una segunda gráfica
   con el conteo de corridas por el metadato `outcome` (`blocked`, `forbidden`, `error`).
3. **Costo por cliente y por modelo.** Sobre las corridas `name = ask`, suma del metadato
   `cost_usd` agrupada por `client` y otra agrupada por `model`.
4. **Volumen por cliente.** Conteo de trazas `ask` agrupado por `client`.

Las etiquetas (el ambiente y los agentes que corrieron) sirven para filtrar: por ejemplo
`production` y `policies` muestran solo las preguntas de producción que llegaron al
especialista de políticas. Ningún metadato lleva la pregunta, la respuesta ni una clave.
