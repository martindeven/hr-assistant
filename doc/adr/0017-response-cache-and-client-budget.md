# 0017. Caché de respuestas y presupuesto diario por cliente, en el proceso

Estado: aceptado.

## Contexto

En un portal de RH las preguntas se repiten, y cada repetición vuelve a pagar el modelo. Además, un solo cliente
puede gastar sin tope. La parte 2 del reto pide dos palancas: una caché de respuestas y un presupuesto diario,
sin saltarse ningún control de seguridad y sin mezclar clientes.

## Decisión

- **Caché** (`RESPONSE_CACHE_TTL_SECONDS`, 0 por omisión; LRU con `RESPONSE_CACHE_MAX_ENTRIES`). Llave: la
  pregunta ya tapada por el guard y normalizada con `text.fold`, los especialistas permitidos al cliente y un
  hash del historial que se manda al modelo. Se busca después del guard, los permisos y el presupuesto. Solo se
  guardan respuestas ya revisadas: nunca errores, tope de pasos, `replaced` ni respaldo. Un acierto responde con
  `cache: response`, `cost_usd: 0` y `steps: 0`.
- **Presupuesto** (`CLIENT_DAILY_BUDGET_USD`, 0 = sin tope). Suma el costo de cada `200` por cliente y día UTC
  (reloj inyectado). Al llegar al tope: `429 cost budget exceeded` con `Retry-After` hasta la medianoche UTC y
  audit `budget_exceeded`. Va después de los permisos y antes de la caché.
- Las dos viven en memoria del proceso, con candado, como el límite de peticiones (ADR 0010).

## Consecuencias

- Una pregunta repetida cuesta 0; `verification evaluate` lo mide contra la línea base.
- Con `WEB_CONCURRENCY` procesos, la tasa de aciertos baja y el presupuesto efectivo se multiplica; un reinicio
  los vacía. Un almacén compartido (Redis) queda fuera del alcance.
- Dos preguntas simultáneas pueden pasar el tope por una respuesta.
- El riesgo de datos viejos se acota con el TTL (300 s en Render): los datos solo cambian al reiniciar.
