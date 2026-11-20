# 0014. Barandales deterministas y en capas

Estado: aceptado.

## Contexto

El producto recibe texto libre de Internet, lo manda a un proveedor externo (Cloudflare) y a trazas
(LangSmith), y devuelve texto generado. Hay que frenar datos personales, inyección de instrucciones, respuestas
inventadas y abuso HTTP, sin depender de que el modelo se porte bien.

## Decisión

- Todas las reglas son código, sin modelo: `pii.py`, `injection.py` (reglas con id en un solo lugar),
  `guard.py`, `reviewer.py` y validadores de argumentos de cada herramienta.
- `guard` revisa la pregunta y **todo** el historial. Inyección -> `400` sin llamar al modelo. PII -> se tapa
  **antes** del enrutador, el modelo, la traza y el log (o `400` en modo `block`).
- `reviewer` revisa la salida: vacía, filtración del prompt, `EMP-####` sin fundamento, PII y largo.
- Orden fijo de capas: encabezados, observabilidad, clave (401), límite (429), tamaño (413), validación (422),
  guard (400), permisos (403), agente, herramientas, reviewer.
- El JS y el CSS de la página salen a `static/` para que la CSP `default-src 'self'` no los bloquee.
- La auditoría guarda tipos, conteos e ids de regla; nunca el texto ni una clave.
- En producción no se puede apagar el guard ni la PII de entrada (ADR 0011).

## Consecuencias

- Predecible, rápido, gratis y con pruebas exactas por regla.
- Habrá falsos positivos y negativos: una regla no entiende paráfrasis. Cada caso nuevo es una regla y una
  prueba; el riesgo restante se anota en `doc/security.md`.
- El modelo nunca ve los datos tapados, así que no puede responder sobre ellos.
