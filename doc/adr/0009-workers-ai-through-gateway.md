# 0009. Modelo real: Workers AI a través de AI Gateway

Estado: aceptado. Amplía el ADR 0002, que solo aceptaba `fake`.

## Contexto

El producto necesita un modelo real sin perder pruebas deterministas ni filtrar secretos. AI
Gateway de Cloudflare da una API compatible con chat completions y caché de respuestas.

## Decisión

- `GatewayModel` en `llm.py` cumple `LanguageModel`. Usa un `httpx.Client` inyectado, timeout de
  30 s, `max_tokens = MODEL_MAX_TOKENS` y `temperature = MODEL_TEMPERATURE` (0 por omisión).
- Antepone un mensaje `system` con el protocolo JSON y las herramientas; `agent.py` no cambia de
  protocolo.
- Con `CACHE_TTL_SECONDS > 0` manda `cf-aig-cache-ttl`; lee `cf-aig-cache-status` para marcar la
  llamada como cacheada.
- Cualquier fallo (red, timeout, estado no 2xx, cuerpo incompleto) es `ModelUnavailableError`, y la
  API responde `502 {"detail": "model unavailable", "request_id"}`.
- Con `MODEL_PROVIDER=gateway`, faltar `CF_ACCOUNT_ID`, `CF_GATEWAY_ID` o `CF_API_TOKEN` impide
  arrancar. El token se guarda con `repr=False` y nunca se registra.

## Consecuencias

- Las pruebas usan `httpx.MockTransport`: ninguna toca la red.
- Un modelo real puede romper el protocolo; lo absorbe el bucle con tope (ADR 0003).
- La caché puede devolver datos viejos del directorio: está apagada por omisión.
