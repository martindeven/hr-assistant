# Análisis de costos del HR Assistant

Este documento explica cada palanca de costo del producto: cuánto ahorra, qué arriesga y cómo se mide. Las cifras
salen de `uv run python -m verification evaluate`, que pregunta el conjunto de evaluación
(`verification/eval_set.json`, 10 preguntas) **dos veces** en tres configuraciones, cada pregunta con un historial
de relleno de 10 mensajes, como en una conversación larga de un portal de RH.

El costo de una respuesta es `(tokens_entrada * precio_entrada + tokens_salida * precio_salida) / 1 000 000`, con
los precios de `data/model_prices.json` (USD por millón de tokens) y solo con las llamadas que se facturan.

## Resultados con el modelo simulado

Corrida con `MODEL_PROVIDER=fake`: los dos modelos simulados responden igual, pero cobran con el precio de
`MODEL_SMALL` y `MODEL_LARGE`. Así las palancas se miden sin red y sin gastar cuota. Los tokens son los que cuenta
el modelo simulado, no los de un modelo real: la tabla sirve para comparar configuraciones, no para presupuestar.

## Evaluate (fake, 2 rondas del conjunto de evaluación)

| Configuración | Calidad | Costo (USD) | Tokens de entrada | Ahorro |
| --- | --- | --- | --- | --- |
| Línea base (modelo grande, sin ventana, sin caché) | 100% | 0.002492 | 4368 | 0% |
| Palancas (enrutamiento + ventana) | 100% | 0.000338 | 2924 | 86% |
| Palancas + caché de respuestas | 100% | 0.000169 | 1462 | 93% |

- **Línea base:** `ROUTING=false`, `HISTORY_WINDOW=1000` y `RESPONSE_CACHE_TTL_SECONDS=0`.
- **Palancas:** la configuración por omisión (enrutamiento y ventana de 6 mensajes), sin caché de respuestas.
- **Palancas + caché:** lo anterior con `RESPONSE_CACHE_TTL_SECONDS=300`. La segunda ronda sale entera de la caché
  (`cache: "response"`, `cost_usd: 0`, `steps: 0`), por eso el costo y los tokens bajan a la mitad.

Ninguna configuración pierde calidad frente a la línea base; las palancas cuestan menos que la línea base y la
caché no cuesta más que las palancas. La verificación pasa (`[OK] evaluate`).

## Resultados con el modelo real (la llena una persona)

Esta sección la llena **una persona**, no un agente: la corrida usa las credenciales de Cloudflare del entorno de
quien la corre, que nunca se escriben en el repositorio ni en el chat. Desde la raíz de la fábrica, con las
variables `CF_*` ya puestas en el entorno:

    uv run python -m verification evaluate --provider gateway

Con el modelo real la calidad puede variar entre corridas aunque `MODEL_TEMPERATURE=0`; si una configuración
pierde calidad, esa palanca no se usa.

### Corrida del 2026-11-20 (Workers AI vía AI Gateway)

Evidencia: `doc/evidence/evaluate-gateway.md` (tabla) y `doc/evidence/evaluate-gateway-detail.md` (pregunta por
pregunta).

| Configuración | Calidad | Costo (USD, 20 preguntas) | Tokens de entrada | Ahorro | Costo por pregunta | Costo por respuesta correcta |
| --- | --- | --- | --- | --- | --- | --- |
| Línea base (70B, sin ventana, sin caché) | 80% | 0.006010 | 12944 | 0% | 0.0003005 | 0.000376 |
| Palancas (enrutamiento + ventana) | 60% | 0.001216 | 15921 | 80% | 0.0000608 | 0.000101 |
| Palancas + caché de respuestas | 50% | 0.000759 | 10310 | 87% | 0.0000380 | 0.000076 |

**Veredicto.** Con el modelo real, la regla de `evaluate` **no se cumple**: las palancas ahorran 80 % y 87 %, pero
pierden 20 y 30 puntos de calidad. La versión simulada (100 % en las tres) no lo podía mostrar, porque sus dos
modelos responden igual.

**Qué falla y por qué** (detalle en la evidencia):

| Falla | Preguntas | Causa | Palanca responsable |
| --- | --- | --- | --- |
| Listas sin terminar («No pude completar la respuesta») | Finanzas, Tecnología | El 8B no cierra el ciclo de herramientas y llega al tope de pasos | Enrutamiento |
| Política inventada (15 días en lugar de 12) | Vacaciones | El 8B no se apega al resultado de `search_policy`; el reviewer solo verifica ids `EMP-####`, no cifras | Enrutamiento |
| Formato inválido | EMP-0005 | El 8B responde un JSON sin `answer` ni `tool` y luego repite el mensaje de error | Enrutamiento |
| No redirige a RH | Clima | Rechaza bien, pero sin mencionar RH; la línea base también la falla a veces | Criterio de evaluación estricto |

Lecturas:

- **El enrutamiento es la palanca que cuesta calidad.** Todas las preguntas del conjunto son cortas, así que la
  regla de `ROUTER_MAX_CHARS` manda las 10 al 8B. La regla mide el largo, pero lo que hace difícil una pregunta es
  la tarea (listar, citar una política).
- **Las palancas usan más tokens y cuestan menos.** El 8B da más vueltas con las herramientas (15 921 contra
  12 944 tokens), pero cuesta unas 6 veces menos por token.
- **La caché no empeora respuestas, las repite.** En la segunda ronda devuelve lo que contestó la primera; si
  estaba mal, cuenta mal dos veces. La diferencia de 10 puntos con «Palancas» es una sola pregunta, dentro de la
  variación del modelo.
- **La línea base tampoco llega al 100 %:** el 70B falla 2 de 10, sobre todo por la forma de la respuesta.

### Ajustes propuestos (por medir)

| Ajuste | Qué resuelve | Costo estimado por pregunta | Ahorro estimado frente a la línea base |
| --- | --- | --- | --- |
| A. Enrutar por tarea: listas de departamento y preguntas de `policies` van al 70B (4 de cada 10) | Listas y política inventada | 0.6 x 0.0000608 + 0.4 x 0.0003005 = 0.000157 | 48% |
| B. `policies` responde con el texto de la política (extractivo, sin modelo) | Política inventada, con costo 0 en ese agente | Menor que A | Mayor que A |
| C. Un reintento cuando el modelo devuelve un JSON inválido, y que el reviewer reemplace mensajes internos | EMP-0005 | Una llamada extra solo cuando falla | Casi igual |
| D. Prompt de `directory`: fuera de tema, redirigir a RH | Clima | 0 | Igual |

Las estimaciones de A suponen el mismo costo por pregunta de la tabla; hay que confirmarlas repitiendo
`verification evaluate --provider gateway` después del cambio.

### Corrección aplicada: grounding de políticas (commit `de83091`)

La revisión pregunta por pregunta encontró un error que no dependía del modelo. Con `ROUTING=false`, el 70B también
respondía «15 días» de vacaciones con `tools_used: []`: el especialista `policies` podía contestar sin consultar la
política, y el reviewer no lo detectaba. Se corrigió con los ajustes B y C, en versión con modelo:

- `policies` ejecuta siempre `search_policy` antes de llamar al modelo.
- El reviewer exige que cada número de la respuesta esté en la política; si no, cita el texto de la política
  (`review: replaced`).
- El reviewer reemplaza los mensajes internos del agente por la ayuda.

| Corrida con el modelo real (10 preguntas, Render) | Ruta | Calidad |
| --- | --- | --- |
| Palancas + caché, antes de la corrección | 8B en todas | 50% |
| `ROUTING=false`, después de la corrección | 70B en todas | 90% |

La única falla que queda es el clima (ajuste D, la prueba es estricta). La calidad supera a la línea base de
`evaluate` (80 %).

### Recomendación para producción

- **Configuración actual en Render:** `ROUTING=false` (siempre el 70B), con la ventana, el tope de salida, la caché
  de respuestas y el presupuesto por cliente encendidos. En RH una respuesta equivocada sobre una política cuesta
  más que la diferencia de precio. Con una tasa de aciertos de caché del 50 %, el costo queda en unos USD 0.00015
  por pregunta (unos USD 4.50 al mes con 1000 preguntas al día), la mitad de la línea base, con 90 % de calidad.
- **Siguiente paso:** el ajuste A (enrutar por tarea). Volver a `ROUTING=true` solo si `evaluate` con el modelo real
  muestra que no se pierde calidad.

## Palancas

### 1. Enrutamiento por pregunta (sesión 3)

- **Qué hace.** Una regla en código elige `MODEL_SMALL` para preguntas cortas y simples, y `MODEL_LARGE` si la
  pregunta pasa de `ROUTER_MAX_CHARS` o pide comparar, resumir, explicar o analizar (`ROUTING`, `ROUTER_MAX_CHARS`).
- **Cuánto ahorra.** Es la palanca más grande: el modelo pequeño cuesta unas 6,5 veces menos por token de entrada
  y unas 5,9 veces menos por token de salida. Junto con la ventana, 86 % en la tabla simulada.
- **Qué arriesga.** Calidad: una pregunta difícil que la regla manda al modelo pequeño puede responderse peor.
- **Cómo se mide.** `route` y `model` en cada respuesta y en `agent_run`; `by_route` y `by_model` en `/metrics`;
  la calidad, con `verification evaluate`.

### 2. Ventana de historial (sesión 3)

- **Qué hace.** Solo se mandan al modelo los últimos `HISTORY_WINDOW` mensajes del historial.
- **Cuánto ahorra.** Tokens de entrada en conversaciones largas: en la tabla, de 4368 a 2924 tokens (33 %) con
  las preguntas de relleno de 10 mensajes.
- **Qué arriesga.** Calidad: el modelo pierde contexto de mensajes viejos (por ejemplo, un id dicho al inicio).
- **Cómo se mide.** `history_sent` en `agent_run`; `input_tokens` en la respuesta y en `/metrics`.

### 3. Tope de salida (sesión 3)

- **Qué hace.** `MODEL_MAX_TOKENS` limita los tokens de salida de cada llamada.
- **Cuánto ahorra.** Acota el peor caso: la salida es la parte cara (unas 8 veces el precio de la entrada en los
  dos modelos). Con el modelo simulado no se nota, porque sus respuestas son cortas.
- **Qué arriesga.** Calidad: una respuesta larga sale cortada.
- **Cómo se mide.** `output_tokens` en la respuesta y en `/metrics`; respuestas cortadas en la evaluación real.

### 4. Caché del gateway (sesión 3)

- **Qué hace.** Con `CACHE_TTL_SECONDS > 0` se pide a AI Gateway que guarde cada llamada; un `HIT` no se cobra.
- **Cuánto ahorra.** El costo de cada llamada idéntica (mismos mensajes) dentro del TTL. Solo aplica con el
  modelo real.
- **Qué arriesga.** Datos viejos: un `HIT` puede traer una respuesta de antes de un cambio; por eso el TTL es
  corto.
- **Cómo se mide.** `cached_calls` y `cache: "gateway"` en la respuesta; `cache.gateway_hits` en `/metrics` y
  `hr_cache_hits_total{layer="gateway"}` en Prometheus.

### 5. Supervisor y guard por reglas (reto, parte 1)

- **Qué hace.** El `guard`, el `supervisor` y el `reviewer` son reglas en código, no llamadas al modelo.
- **Cuánto ahorra.** Cada pregunta paga solo los especialistas que corren. Un supervisor con modelo agregaría al
  menos una llamada por pregunta; una inyección bloqueada (`400`) o un permiso negado (`403`) cuestan 0.
- **Qué arriesga.** Calidad del enrutamiento: una pregunta con palabras que la regla no conoce va a
  `directory`, que contesta con la ayuda.
- **Cómo se mide.** `agents` en la respuesta y `by_agent` en `/metrics`: solo los especialistas suman
  llamadas (`by_model.calls`).

### 6. Caché de respuestas (reto, parte 2)

- **Qué hace.** Con `RESPONSE_CACHE_TTL_SECONDS > 0` (Render usa 300) se guarda la respuesta revisada. La llave
  es la pregunta ya revisada por el guard y normalizada (sin mayúsculas, sin acentos, espacios juntos), los
  especialistas permitidos al cliente y un hash del historial que se manda al modelo. Hasta
  `RESPONSE_CACHE_MAX_ENTRIES` entradas; cuando se llena sale la menos usada (LRU).
- **Cuánto ahorra.** El 100 % de una pregunta repetida: no llama al modelo (`cost_usd: 0`, `steps: 0`). En la
  tabla, la mitad del costo de las palancas, porque la mitad de las preguntas se repiten.
- **Qué arriesga.**
  - Datos viejos: los datos se cargan al arrancar, así que dentro de un proceso no cambian; el riesgo real es un
    cambio de prompt o de modelo sin reinicio, acotado por el TTL.
  - Mezclar clientes: no pasa, porque la llave lleva los permisos y la búsqueda va después del guard, los permisos
    y el presupuesto. Una inyección sigue dando `400`.
  - Guardar algo malo: no se guardan errores, respuestas por tope de pasos, respuestas reemplazadas por el
    reviewer ni respuestas de respaldo.
  - Con varios procesos cada uno tiene su caché y la tasa de aciertos baja.
- **Cómo se mide.** `cache: "response"` en la respuesta y en `agent_run`; `cache.response_hits` y
  `cache.hit_rate` en `/metrics`; `hr_cache_hits_total{layer="response"}` en Prometheus; la tasa de caché del
  tablero.

### 7. Presupuesto diario por cliente (reto, parte 2)

- **Qué hace.** Con `CLIENT_DAILY_BUDGET_USD > 0`, cuando el costo del día (UTC) de un cliente alcanza el tope,
  sus siguientes preguntas responden `429 {"detail": "cost budget exceeded"}` con `Retry-After` (segundos hasta la
  medianoche UTC) y se audita `budget_exceeded`. Se revisa después de los permisos y antes de la caché.
- **Cuánto ahorra.** No baja el costo de una respuesta: pone un techo al gasto diario de cada cliente (una
  integración con un error que pregunta en bucle no gasta más que su tope).
- **Qué arriesga.** Disponibilidad: un cliente legítimo se queda sin respuestas hasta la medianoche UTC. Es del
  proceso: con `WEB_CONCURRENCY` procesos el tope efectivo se multiplica, se reinicia al reiniciar y dos preguntas
  simultáneas pueden pasarlo por una respuesta.
- **Cómo se mide.** Eventos `audit` con `action: budget_exceeded`; respuestas `429` en el log `request`;
  `by_client.cost_usd` en `/metrics` contra el tope.

## Proyección mensual (1000 preguntas al día)

Supuestos: 30 días (30 000 preguntas al mes), el costo por pregunta de la tabla simulada (20 preguntas por
configuración) y, para la caché, la misma tasa de aciertos que en la evaluación (50 %).

| Configuración | Costo por pregunta (USD) | Costo mensual (USD) |
| --- | --- | --- |
| Sin palancas (línea base) | 0.0001246 | 3.74 |
| Con palancas (enrutamiento y ventana) | 0.0000169 | 0.51 |
| Con palancas y caché de respuestas (50 % de aciertos) | 0.0000085 | 0.25 |

Con el modelo real (corrida del 2026-11-20, 20 preguntas por configuración):

| Configuración | Calidad | Costo por pregunta (USD) | Costo mensual (USD) |
| --- | --- | --- | --- |
| Línea base (70B siempre) | 80% | 0.0003005 | 9.02 |
| Palancas (enrutamiento y ventana) | 60% | 0.0000608 | 1.82 |
| Palancas y caché de respuestas | 50% | 0.0000380 | 1.14 |
| Recomendada: 70B, ventana y caché (50 % de aciertos, estimado) | ~80% | ~0.00015 | ~4.50 |
| Ajuste A con caché (estimado) | ~80% | ~0.000079 | ~2.40 |

Para el modelo simulado son órdenes de magnitud, no un presupuesto: los tokens del modelo simulado no son los del modelo real y la tasa de
aciertos real depende de cuánto se repitan las preguntas y de cuántos procesos haya. Para presupuestar, rehaz la
cuenta con la tabla del modelo real y con `cache.hit_rate` y `cost_usd_per_request` de `/metrics` en producción.
