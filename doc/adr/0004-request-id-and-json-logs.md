# 0004. Un request_id por petición y log JSON sin datos personales

Estado: aceptado

## Contexto

Soporte necesita responder "qué pasó con la petición X" con datos. El log debe poder filtrarse
por petición, pero el asistente maneja datos de empleados: el texto de preguntas, respuestas y
errores no puede quedar escrito.

## Decisión

- Un middleware resuelve el `request_id`: usa `X-Request-ID` del cliente si tiene de 1 a 128
  caracteres de `[A-Za-z0-9._-]`; si no, genera un UUID4. Lo devuelve en el encabezado de toda
  respuesta (incluidas 422 y 500) y en el cuerpo de `POST /ask`.
- El id viaja explícito (`request.state` y parámetros), sin variables globales.
- Log en la salida estándar, una línea JSON por evento con `ts` (UTC, reloj inyectado),
  `service`, `event` y `request_id`. Eventos: `request`, `agent_run`, `audit` y `error`.
- Solo conteos y metadatos: nunca el texto de la pregunta, la respuesta ni el mensaje de una
  excepción (solo `error_type`). Un `500` responde `{"detail": "internal error", "request_id"}`.
- Las excepciones se atrapan dentro del middleware, para que el `500` también lleve encabezado y
  evento `request`.

## Consecuencias

- Cualquier petición se reconstruye con un solo filtro por `request_id`.
- Para depurar el contenido de una conversación hacen falta las trazas (ADR 0006), no el log.
- El log de acceso de uvicorn se apaga para que stdout tenga solo JSON.
