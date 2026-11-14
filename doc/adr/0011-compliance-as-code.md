# 0011. Compliance como código: producción se niega a arrancar si es insegura

Estado: aceptado.

## Contexto

Una lista de revisión en un documento se olvida. En producción hay errores que no deben llegar a
atender a nadie: el modelo simulado, la API sin claves o con claves débiles, y trazas que mandan a
LangSmith preguntas y respuestas con datos de empleados.

## Decisión

- `ENVIRONMENT` vale `development` (omisión) o `production`; otro valor es error.
- Con `production`, `compliance.check_production` corre dentro de `load_settings` y lanza un solo
  `ConfigError` con todas las reglas que fallan:
  - `MODEL_PROVIDER` es `fake`.
  - `API_KEYS` está vacía o alguna clave tiene menos de 16 caracteres.
  - Las trazas están encendidas y `HIDE_INPUTS` o `HIDE_OUTPUTS` no son `true`.
- Como `app = create_app()` corre al importar, el proceso termina con código distinto de 0 y Render
  no da el servicio por sano.
- Los mensajes nombran la regla, el cliente y el largo; nunca el valor de una clave.
- `doc/compliance-checklist.md` tiene la tabla: regla, dónde está en el código, cómo se comprueba.

## Consecuencias

- Un error de configuración en Render se ve en el log de arranque, no en una respuesta a un usuario.
- En desarrollo nada cambia: se puede usar el modelo simulado y claves cortas.
- Cada regla nueva necesita su prueba y su fila en la tabla.
