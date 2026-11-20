# HR Assistant

Asistente de Recursos Humanos: una API web sin estado (FastAPI) con una página que responde
preguntas sobre empleados y departamentos. Un agente con bucle de tope usa dos herramientas de
solo lectura (`get_employee` y `search_by_department`) sobre `data/employees.json`. El modelo
puede ser simulado (`fake`, determinista y sin red) o real (`gateway`: Cloudflare Workers AI a
través de AI Gateway).

Desde la sesión 2 cada petición se puede rastrear: tiene un `request_id`, deja un log JSON en
la salida estándar, alimenta `GET /metrics` y, si se enciende, manda trazas a LangSmith.

Desde la sesión 3 cada respuesta dice qué modelo usó y cuánto costó, y cuatro palancas bajan el
costo sin bajar la calidad (ver «Costos»).

Desde la sesión 4 está listo para producción: `POST /ask` y `GET /metrics` piden una clave por
cliente, cada cliente tiene un límite de peticiones por minuto y, con `ENVIRONMENT=production`,
el producto se niega a arrancar con una configuración insegura (ver «Acceso y seguridad»).

Desde el reto (parte 1) es un **sistema multiagente**: cada pregunta pasa por `guard`
(revisa la entrada), `supervisor` (elige especialistas con reglas), los especialistas
`directory` (empleados) y `policies` (políticas de RH de `data/policies.json`) y `reviewer`
(revisa la salida). Además reintenta las fallas transitorias del modelo, responde con el modelo
pequeño si el grande sigue fallando y corre con varios procesos (ver «Multiagente» y
«Concurrencia y resiliencia»).

La arquitectura y las decisiones (ADR) están en `doc/`.

## Cómo correrlo

Desde la carpeta `product/`, con una clave de cliente en `API_KEYS` (sin ella, todas las
preguntas responden `401`):

```bash
uv pip install -r requirements.txt
export API_KEYS="local:<una clave tuya de 16 caracteres o más>"
uv run uvicorn app.main:app --port 8000
```

Abre `http://127.0.0.1:8000/` en el navegador y escribe la clave en el campo «Clave de acceso».
Las pruebas, con cobertura, se corren con:

```bash
uv run pytest tests --cov=app
```

## Endpoints

| Método y ruta | Clave | Qué hace |
| --- | --- | --- |
| `GET /` | No | Página web; muestra el `request_id` de cada respuesta para pasarlo a soporte |
| `GET /health` | No | `{"status": "ok", "pid": ...}` |
| `GET /ready` | No | `{"status": "ready", "checks": {"directory", "policies", "prices"}}`; no llama al modelo |
| `GET /static/app.js`, `GET /static/app.css` | No | Código y estilos de la página (sin código en línea) |
| `GET /docs` | No | Documentación interactiva de la API (usa `/openapi.json`, también pública) |
| `POST /ask` | Sí | Recibe `{"question", "history"}` y devuelve `answer`, `steps`, `tools_used`, `input_tokens`, `output_tokens`, `request_id`, `model`, `route`, `cached_calls`, `cost_usd`, `agents`, `guardrails` y `fallback` |
| `GET /metrics` | Sí, con permiso `metrics` | Métricas de este proceso: `pid`, `requests`, `error_rate`, `latency_ms` (`p50`, `p95`), `tokens`, `cost_usd` y `slo` |

Cualquier otra ruta también pide clave (se niega por omisión).

`POST /ask` responde `422` si la pregunta está vacía o pasa de `MAX_QUESTION_CHARS`, si
`history` trae más de `MAX_HISTORY_MESSAGES` mensajes o si algún `content` pasa de
`MAX_HISTORY_CHARS` caracteres. La página manda solo los últimos mensajes permitidos, cada uno
recortado a ese largo, para no chocar con los topes en una conversación larga.

Toda respuesta trae el encabezado `X-Request-ID`. Si el cliente manda uno de 1 a 128 caracteres
de `[A-Za-z0-9._-]`, se usa ese; si no, se genera un UUID. Si algo falla, la respuesta es `500`
con `{"detail": "internal error", "request_id": ...}`.

Si el modelo no responde (red, tiempo de espera de `MODEL_TIMEOUT_SECONDS` por llamada, estado
que no es 2xx o cuerpo incompleto), primero se reintenta hasta `MODEL_RETRIES` veces con una
espera inicial de `MODEL_RETRY_BACKOFF_MS` que se duplica y, con `MODEL_FALLBACK=true`, se
responde con `MODEL_SMALL` (ver «Concurrencia y resiliencia»). Si aun así falla, la respuesta
es `502` con `{"detail": "model unavailable", "request_id": ...}`.

## Multiagente

Cada `POST /ask` corre estos agentes, en orden, y la respuesta los lista en `agents`:

| Agente | Qué hace | ¿Usa modelo? |
| --- | --- | --- |
| `guard` | Revisa la pregunta y el historial (datos personales e inyección) | No |
| `supervisor` | Elige los especialistas (sin distinguir mayúsculas ni acentos) | No |
| `directory` | Herramientas `get_employee` y `search_by_department` | Sí |
| `policies` | Herramienta `search_policy(topic)` sobre `POLICIES_PATH` | Sí |
| `reviewer` | Revisa la respuesta final | No |

Reglas del supervisor: un id `EMP-####` o un departamento que existe (palabra completa) va a
`directory`; una palabra clave de una política va a `policies`; las dos, a `directory` y después
a `policies` (las respuestas se juntan con un salto de línea y `steps` y `tools_used` se suman);
ninguna, a `directory`, que contesta con la ayuda. El enrutador elige el modelo una sola vez y
lo usan todos los especialistas de la pregunta.

Cada especialista solo ejecuta sus herramientas: si el modelo pide una ajena o que no existe,
no se ejecuta, el modelo recibe un error y se audita `tool_denied`. `guardrails` trae
`pii_input`, `pii_output` (tipos, nunca valores) y `review` (`passed`, `redacted` o
`replaced`). La página muestra los agentes debajo de cada respuesta y, si se ocultaron datos,
«Se ocultaron datos sensibles».

## Concurrencia y resiliencia

- **Varios procesos.** El contenedor arranca uvicorn con `--workers ${WEB_CONCURRENCY:-2}`. Cada
  proceso arma su propia aplicación: las métricas de `GET /metrics` y el límite de peticiones
  son **de cada proceso**. Con 2 procesos, `/metrics` muestra solo lo que vio el proceso que
  respondió (el campo `pid` dice cuál) y el límite efectivo por cliente puede llegar a
  `RATE_LIMIT_PER_MINUTE × WEB_CONCURRENCY`. Dentro de un proceso, `POST /ask` no comparte
  estado mutable entre peticiones; el log, las métricas y el límite usan candados.
- **Reintentos.** `GatewayModel` hace hasta `1 + MODEL_RETRIES` intentos. Espera
  `MODEL_RETRY_BACKOFF_MS` y duplica la espera en cada intento. Solo reintenta con tiempo de
  espera agotado, error de red, `429` o `5xx`; nunca con otro `4xx` ni con un cuerpo inválido.
- **Respaldo.** Con `MODEL_FALLBACK=true`, si la ruta eligió `MODEL_LARGE` y sigue fallando
  después de los reintentos, la pregunta se responde desde el principio con `MODEL_SMALL`:
  `fallback: true`, `model` dice el modelo pequeño, el costo usa su precio y se audita
  `fallback`. Si también falla, la respuesta es `502`.
- **Tiempo de espera.** Cada llamada espera como máximo `MODEL_TIMEOUT_SECONDS`. En el peor caso
  una pregunta espera 3 intentos en el modelo grande y otros 3 en el pequeño: en Render conviene
  un valor menor que el de omisión.

## Acceso y seguridad

**Claves por cliente.** `API_KEYS` tiene la forma `cliente:clave,cliente2:clave2`. Se quitan los
espacios alrededor de cada entrada y se parte en el primer `:` (la clave puede tener `:`). Para
dar acceso a alguien, agrega una entrada con un nombre de cliente nuevo y una clave larga y
aleatoria (por ejemplo `python -c "import secrets; print(secrets.token_urlsafe(32))"`), y
reinicia el servicio. Para quitarlo, borra su entrada. Cada cliente manda su clave en el
encabezado `X-API-Key`; la página la pide en el campo «Clave de acceso» y la guarda solo en
`sessionStorage` de la pestaña.

- Sin clave, con una vacía o con una que no existe: `401 {"detail": "invalid or missing API key"}`.
  La clave se revisa antes que el cuerpo, así que sin clave nunca hay `422`.
- Las claves se comparan con `hmac.compare_digest` contra todas, sin salir antes.
- El nombre del cliente va en el campo `client` de los eventos `request`, `agent_run` y `audit`
  y en los metadatos de la traza. **La clave nunca** se escribe en el log, la traza ni un error.
- Si `API_KEYS` está mal formada (entrada vacía, sin `:`, cliente o clave vacíos, cliente o
  clave repetidos), el producto no arranca. El error dice el número de entrada y su largo, por
  ejemplo `API_KEYS entry 2 is malformed (length 27)`, nunca el valor.

**Límite de peticiones.** Cada cliente puede hacer `RATE_LIMIT_PER_MINUTE` peticiones en
cualquier ventana de 60 segundos. La siguiente responde `429 {"detail": "rate limit exceeded"}`
con `Retry-After` (segundos que faltan, al menos 1). Solo cuentan las peticiones aceptadas. El
límite vive en la memoria de cada proceso: con varios procesos o instancias, el límite real se
multiplica.

**Compliance.** Con `ENVIRONMENT=production`, el producto se niega a arrancar si el modelo es
`fake`, si `API_KEYS` está vacía o alguna clave tiene menos de 16 caracteres, si las trazas
están encendidas y `HIDE_INPUTS` o `HIDE_OUTPUTS` no valen `true`, si `PII_INPUT_MODE=off`, si
`INJECTION_GUARD=false` o si algún cliente de `API_KEYS` no tiene entrada en `API_KEY_SCOPES`.
El error junta todas las reglas que fallan y nombra la variable o el cliente, nunca un valor. La
tabla de reglas está en `doc/compliance-checklist.md`.

## Seguridad en capas

Una petición pasa por estos controles, en este orden: clave (`401`), límite (`429`), tamaño del
cuerpo (`413`), validación (`422`), `guard` (`400`) y permisos (`403`). Después corren los
especialistas, solo con sus herramientas, y al final el `reviewer`. El modelo de amenazas
(OWASP Top 10 para aplicaciones con LLM) está en `doc/security.md`.

- **Datos personales.** El `guard` reconoce correo, teléfono (10 dígitos, `+52` opcional), CURP,
  RFC, NSS (11 dígitos), tarjeta (13 a 19 dígitos que pasan Luhn) y CLABE (18 dígitos), y los
  tapa con su etiqueta (`[EMAIL]`, `[PHONE]`, ...). Con `PII_INPUT_MODE=redact` (omisión) la
  pregunta y el historial se tapan antes de llegar al enrutador, al modelo, a la traza y al
  log; con `block` la respuesta es `400 {"detail": "request contains sensitive data",
  "request_id": ...}`; con `off` no se revisa. La respuesta final siempre se tapa.
- **Inyección de instrucciones.** Con `INJECTION_GUARD=true`, si la pregunta o cualquier
  mensaje del historial (también los `assistant`, que manda el cliente) cumple una regla de
  `app/injection.py`, la respuesta es `400 {"detail": "request blocked by policy",
  "request_id": ...}` y el modelo no se llama. Los argumentos de las herramientas se validan:
  `employee_id` con `^EMP-\d{4}$`, `department` y `topic` de 60 caracteres como máximo.
- **Revisión de la salida.** El `reviewer` cambia una respuesta vacía por la ayuda, una que
  filtra el inicio de un prompt de sistema por un mensaje seguro y una que menciona un
  `EMP-####` sin evidencia (ni en la pregunta ni en una herramienta) por «No pude verificar la
  respuesta con el directorio.»; tapa los datos personales y recorta a `MAX_ANSWER_CHARS`.
- **Permisos por cliente.** `API_KEY_SCOPES` tiene la forma `cliente:permiso permiso,...` con
  los permisos `directory`, `policies` y `metrics`. El supervisor solo corre los especialistas
  permitidos; si no queda ninguno, `403 {"detail": "forbidden for this client", "request_id":
  ...}`. Sin `metrics`, `GET /metrics` responde `403`. En `development` un cliente sin entrada
  tiene todos los permisos. Si la variable está mal formada, nombra un permiso desconocido, un
  cliente que no está en `API_KEYS` o repite un cliente, el producto no arranca. Ejemplo:
  `API_KEY_SCOPES="portal:directory policies metrics,kiosco:policies"`. Un kiosco solo con
  `policies` recibe `403` en una pregunta sin temas de política, porque esa va a `directory`.
- **Encabezados.** Toda respuesta lleva `X-Content-Type-Options: nosniff`, `X-Frame-Options:
  DENY` y `Referrer-Policy: no-referrer`; `/ask` además `Cache-Control: no-store`; `GET /`
  lleva `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'`, y en
  `production` toda respuesta lleva `Strict-Transport-Security`.
- **Tamaño del cuerpo.** Un cuerpo de más de `MAX_BODY_BYTES` responde `413 {"detail":
  "request too large"}`: con `Content-Length` sin leerlo; sin él, se corta al pasar el tope.
- **Auditoría.** Eventos `audit` de `tool_call`, `tool_denied`, `auth_failed` (`client: null` y
  `path`), `rate_limited`, `forbidden`, `pii_detected` (`where`, `types`, `count`),
  `injection_blocked` (`rule`), `output_reviewed` (`rule`) y `fallback`. Ninguno lleva el texto
  de la pregunta, de la respuesta ni una clave; los `args` de `tool_call` van tapados y
  recortados.

La página muestra «La pregunta no se puede procesar» con `400` y «No tienes permiso para esa
consulta» con `403`, siempre con `textContent`.

## Cómo publicar

1. Una persona aprueba la publicación; `scripts/publish.py` crea el repositorio en GitHub y hace
   el push.
2. GitHub Actions corre cinco etapas encadenadas: `style` -> `security` -> `unit` -> `e2e` ->
   `docker`; si una falla, las siguientes no corren. `security` revisa las dependencias con
   `pip-audit`, el código con `bandit -ll` (severidad media o alta) y el historial con `gitleaks`
   (secretos). Render publica solo si todo pasa.
3. En Render > Environment escribe los secretos `API_KEYS` (claves de 16 caracteres o más),
   `API_KEY_SCOPES` (una entrada por cada cliente de `API_KEYS`), `CF_ACCOUNT_ID` y
   `CF_API_TOKEN`. Nunca van en el repositorio. Si el gateway no se llama
   `default`, agrega `CF_GATEWAY_ID`; si enciendes las trazas, agrega `LANGSMITH_API_KEY`.
4. Comprueba el servicio con la prueba de humo, con la clave en `SMOKE_API_KEY`:
   `uv run python -m verification production <url>`.

### Prueba de carga

`scripts/load_test.py` manda preguntas a `POST /ask` con varias peticiones al mismo tiempo y
sirve como evidencia de concurrencia contra Render:

    LOAD_API_KEY=<clave> python scripts/load_test.py <url> --requests N --concurrency C

`--requests` (por omisión 80) es cuántas preguntas manda y `--concurrency` (por omisión 16)
cuántas van al mismo tiempo. La clave se lee solo de la variable `LOAD_API_KEY`, nunca de un
argumento (quedaría en el historial de la terminal), y nunca se imprime.

Al terminar imprime una tabla Markdown con el total de peticiones, la concurrencia, cuántas
respondieron cada código (las que no tuvieron respuesta salen como «sin respuesta»), las
respuestas `429`, la latencia p50, p95 y máxima en milisegundos y cuántos procesos distintos
(`pid` de `GET /health`) respondieron, con sus `pid`.

Códigos de salida: `0` si todas las preguntas respondieron `200`, `1` si alguna no y `2` si
falta `LOAD_API_KEY`.

## Variables de entorno

`.env.example` lista todas las variables con sus valores por omisión y los secretos vacíos. El
producto nunca lee `.env`: las variables se pasan por el entorno.

| Variable | Por omisión | Para qué |
| --- | --- | --- |
| `MODEL_PROVIDER` | `fake` | Proveedor del modelo: `fake` o `gateway` |
| `CF_ACCOUNT_ID` | (sin valor) | Cuenta de Cloudflare; obligatoria con `gateway` |
| `CF_GATEWAY_ID` | `default` | Id del AI Gateway; obligatorio con `gateway` |
| `CF_API_TOKEN` | (sin valor) | Token de Cloudflare; obligatorio con `gateway`, secreto, nunca se registra |
| `MODEL_SMALL` | `@cf/meta/llama-3.1-8b-instruct-fast` | Modelo barato (debe tener precio) |
| `MODEL_LARGE` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | Modelo capaz (debe tener precio) |
| `ROUTING` | `true` | Enrutamiento por pregunta; con `false` todo va a `MODEL_LARGE` |
| `ROUTER_MAX_CHARS` | `80` | Preguntas más largas van al modelo capaz |
| `HISTORY_WINDOW` | `6` | Mensajes del historial que se mandan al modelo (0: ninguno) |
| `MODEL_MAX_TOKENS` | `300` | Tope de tokens de salida por llamada |
| `MODEL_TEMPERATURE` | `0` | Temperatura de 0 a 2; con 0 las evaluaciones son repetibles |
| `CACHE_TTL_SECONDS` | `0` | Caché del gateway en segundos (0: apagada) |
| `PRICES_PATH` | `data/model_prices.json` | Precios por modelo (relativo a `product/`) |
| `AGENT_MAX_STEPS` | `3` | Tope de llamadas al modelo por pregunta |
| `MAX_QUESTION_CHARS` | `2000` | Largo máximo de la pregunta |
| `MAX_HISTORY_MESSAGES` | `20` | Máximo de mensajes en `history` de `POST /ask` |
| `MAX_HISTORY_CHARS` | `4000` | Largo máximo del `content` de cada mensaje de `history` |
| `EMPLOYEES_PATH` | `data/employees.json` | Archivo de empleados (relativo a `product/`) |
| `SERVICE_NAME` | `hr-assistant` | Campo `service` del log |
| `SLO_P95_MS` | `2000` | Meta de latencia p95 en milisegundos |
| `LANGSMITH_TRACING` | `false` | Enciende las trazas (requiere `LANGSMITH_API_KEY`) |
| `LANGSMITH_API_KEY` | (sin valor) | Clave de LangSmith; se lee del entorno y nunca se guarda ni se registra |
| `HIDE_INPUTS` | `true` | No mandar a LangSmith las entradas (preguntas) |
| `HIDE_OUTPUTS` | `true` | No mandar a LangSmith las salidas (respuestas) |
| `ENVIRONMENT` | `development` | `development` o `production`; con `production` se aplican las reglas de compliance |
| `API_KEYS` | (sin valor) | Clientes y claves, `cliente:clave,cliente2:clave2`; secreto, nunca se registra |
| `RATE_LIMIT_PER_MINUTE` | `30` | Peticiones por cliente por minuto (entero positivo) |
| `POLICIES_PATH` | `data/policies.json` | Políticas de RH (relativo a `product/`) |
| `WEB_CONCURRENCY` | `2` | Procesos de uvicorn en el contenedor |
| `MODEL_RETRIES` | `2` | Reintentos ante fallas transitorias del modelo (entero de 0 en adelante) |
| `MODEL_RETRY_BACKOFF_MS` | `200` | Espera inicial entre reintentos; se duplica |
| `MODEL_FALLBACK` | `true` | Responder con `MODEL_SMALL` si `MODEL_LARGE` falla |
| `MODEL_TIMEOUT_SECONDS` | `30` | Tope de espera de cada llamada al modelo (mayor que 0) |
| `PII_INPUT_MODE` | `redact` | `redact`, `block` u `off` |
| `INJECTION_GUARD` | `true` | Bloquear inyección de instrucciones |
| `MAX_ANSWER_CHARS` | `1500` | Largo máximo de la respuesta |
| `MAX_BODY_BYTES` | `65536` | Tamaño máximo del cuerpo de una petición |
| `API_KEY_SCOPES` | (sin valor) | Permisos de cada cliente (`directory`, `policies`, `metrics`) |

Los booleanos aceptan solo `true` o `false` (sin distinguir mayúsculas). Un valor inválido
detiene el arranque con un mensaje que nombra la variable. Sin `LANGSMITH_API_KEY`, las trazas
quedan apagadas y el producto no intenta conectarse.

## Costos

Cada respuesta de `POST /ask` trae:

- `model`: el modelo que eligió el enrutador.
- `route`: el motivo. `routing_disabled` (con `ROUTING=false`), `long_question` (la pregunta
  pasa de `ROUTER_MAX_CHARS`), `keyword:<palabra>` (contiene «compara», «resume», «explica»,
  «por qué», «analiza» o «todos», sin distinguir mayúsculas ni acentos) o `simple` (va a
  `MODEL_SMALL`).
- `cached_calls`: llamadas que el gateway sirvió de su caché (`cf-aig-cache-status: HIT`).
- `cost_usd`: `(tokens_entrada * precio_entrada + tokens_salida * precio_salida) / 1 000 000`,
  con los precios de `PRICES_PATH` (USD por millón de tokens) y solo con las llamadas no
  cacheadas. No se redondea: con el modelo simulado una respuesta cuesta del orden de 1e-6 USD.

`GET /metrics` suma el `cost_usd` de todas las respuestas del proceso. Con `MODEL_PROVIDER=fake`
hay dos modelos simulados que se llaman como `MODEL_SMALL` y `MODEL_LARGE`: responden igual, pero
cobran con el precio de su nombre, así que las palancas se miden sin red.

Las palancas: enrutamiento (`ROUTING`, `ROUTER_MAX_CHARS`), ventana de historial
(`HISTORY_WINDOW`), tope de salida (`MODEL_MAX_TOKENS`) y caché del gateway
(`CACHE_TTL_SECONDS`, con un TTL corto: un `HIT` puede traer datos de antes de un cambio).

Los precios cambian: revisa `data/model_prices.json` contra la página de precios de Workers AI
antes de usarlo. Si `MODEL_SMALL` o `MODEL_LARGE` no tienen precio, el producto no arranca.

## Cómo leer el log

Cada línea de la salida estándar es un JSON con `ts` (ISO 8601 en UTC), `service`, `event` y
`request_id`. El log de acceso de uvicorn se apaga para que la salida tenga solo estas líneas.
Eventos:

- `request`: `method`, `path`, `status`, `latency_ms` y `client` (`null` en rutas públicas o si
  la clave no sirve); siempre el último de cada petición.
- `agent_run`: `client`, `question_chars`, `history_messages`, `steps`, `tools`, `input_tokens`,
  `output_tokens`, `stopped`, `model`, `route`, `history_sent` (mensajes del historial que sí
  se mandaron), `cached_calls`, `cost_usd`, `agents`, `fallback` y `review`. Nunca el texto de
  la pregunta ni de la respuesta.
- `audit`: cada acción relevante, con `client`, `request_id`, `action` y `outcome`. Por ejemplo
  `tool_call` (una por herramienta que pide el agente, con `tool` y `args` tapados y
  recortados), `tool_denied`, `auth_failed`, `rate_limited`, `forbidden`, `pii_detected`,
  `injection_blocked`, `output_reviewed` y `fallback` (ver «Seguridad en capas»). Nunca lleva el
  texto de la pregunta, de la respuesta ni una clave.
- `error`: el tipo de error en `error_type`, nunca su mensaje.

Para saber qué pasó con una petición, busca su `request_id` en el log (y, con trazas
encendidas, en los metadatos de LangSmith).
