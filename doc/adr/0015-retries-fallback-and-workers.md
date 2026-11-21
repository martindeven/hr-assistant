# 0015. Reintentos, respaldo al modelo pequeño y varios procesos

Estado: aceptado.

## Contexto

Workers AI falla a veces (tiempo agotado, `429`, `5xx`) y hoy cada falla es un `502`. Además, un solo proceso
de uvicorn limita cuántas preguntas se atienden a la vez.

## Decisión

- `GatewayModel` hace `1 + MODEL_RETRIES` intentos con espera `MODEL_RETRY_BACKOFF_MS * 2**n` y un `sleep`
  inyectado. Solo reintenta con tiempo agotado, error de red, `429` o `5xx`; nunca con otro `4xx`. El tope de
  cada llamada es `MODEL_TIMEOUT_SECONDS`.
- Con `MODEL_FALLBACK=true`, si la ruta eligió `MODEL_LARGE` y sigue fallando, la pregunta se reinicia completa
  con `MODEL_SMALL`: `fallback: true`, audit `fallback`, `model` es el que respondió y el costo usa su precio. Si
  también falla, `502`.
- El contenedor arranca `--workers ${WEB_CONCURRENCY:-2}`. Cada proceso tiene su `create_app()`; el estado
  mutable (log, métricas, límite) tiene candado y es del proceso.

## Consecuencias

- Menos `502` visibles; las pruebas siguen sin esperar de verdad.
- El peor caso de latencia crece (intentos x timeout, dos modelos); se documenta y se ajusta con
  `MODEL_TIMEOUT_SECONDS`.
- Al reiniciar con el modelo pequeño, las herramientas del primer intento quedan auditadas dos veces.
- Métricas y límite por proceso (ADR 0005, ADR 0010): el límite efectivo se multiplica por los procesos y
  `/metrics` muestra solo el proceso que respondió. Un almacén compartido queda fuera del alcance.
