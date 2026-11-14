# 0006. Trazas de LangSmith opcionales y apagadas por omisión

Estado: aceptado

## Contexto

El log no guarda contenido (ADR 0004), pero a veces hace falta ver cada paso del agente. LangSmith
da trazas con `@traceable`, pero manda datos a un tercero y necesita una clave. El producto debe
funcionar igual sin ella, y las pruebas no pueden tocar la red.

## Decisión

- Un `Tracer` inyectable en `create_app`. Se enciende solo si `LANGSMITH_TRACING=true` y existe
  `LANGSMITH_API_KEY`; apagado, no decora ninguna función ni crea un `Client`.
- Encendido, se trazan la corrida del agente, cada llamada al modelo y cada herramienta, con el
  `request_id` en los metadatos (`tracing_context`).
- `HIDE_INPUTS` y `HIDE_OUTPUTS` valen `true` por omisión y se pasan explícitos al `Client`.
- La clave se lee del entorno por `langsmith`; no se guarda en `Settings`, ni en el log, ni en
  el código.

## Consecuencias

- Por omisión no sale ningún dato del proceso.
- Con las entradas y salidas ocultas, las trazas muestran estructura, tiempos y errores, no
  contenido. Mostrar contenido es una decisión explícita de configuración.
- Las pruebas usan el `Tracer` apagado o un `Client` falso.
