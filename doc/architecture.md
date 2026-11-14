# Arquitectura del HR Assistant

Un asistente de RH: una página web, una API FastAPI sin estado y un solo agente con dos
herramientas de solo lectura sobre `data/employees.json`. Desde la sesión 2, cada petición tiene
un `request_id`, deja un log JSON y alimenta las métricas del proceso; las trazas de LangSmith son
opcionales. Desde la sesión 3, cada pregunta se enruta a un modelo pequeño o grande, el historial
se recorta a una ventana, la salida tiene tope y cada respuesta dice cuánto costó. El modelo real
es Workers AI a través de AI Gateway; el simulado sigue siendo el de omisión. Desde la sesión 4,
cada cliente entra con su clave y tiene un límite de peticiones; en producción el producto se
niega a arrancar si su configuración es insegura, y se publica en Render desde un contenedor solo
si el pipeline de CI pasa.

## Petición

```mermaid
flowchart LR
    U[Personal de RH] --> P["Página web<br/>static/index.html<br/>(clave en sessionStorage,<br/>historial, request_id)"]
    P -- "POST /ask + X-API-Key" --> MW["middleware.py<br/>request_id, latencia,<br/>errores 500"]
    MW --> SE["security.py<br/>rutas públicas, clave,<br/>401"]
    SE -- "cliente" --> RL["ratelimit.py<br/>ventana de 60 s<br/>por cliente, 429"]
    RL --> M["main.py<br/>create_app / FastAPI<br/>ventana de historial, 502"]
    M -- "422 si la entrada es inválida" --> MW
    M -- "pregunta" --> R["router.py<br/>regla: pequeño o grande"]
    R -- "model, route" --> M
    M -- "modelo elegido" --> A["agent.py<br/>Agent: bucle con tope"]
    A -- "complete(messages)" --> L["llm.py<br/>LanguageModel"]
    L -.implementa.-> F["FakeModel x2<br/>(MODEL_PROVIDER=fake)"]
    L -.implementa.-> G["GatewayModel<br/>(MODEL_PROVIDER=gateway)"]
    G -- "HTTPS, max_tokens,<br/>cf-aig-cache-ttl" --> CF[("Cloudflare AI Gateway<br/>Workers AI")]
    A -- "get_employee / search_by_department" --> D["directory.py<br/>EmployeeDirectory"]
    D --> J[("data/employees.json")]
    M -- "tokens facturables" --> PR["pricing.py<br/>PriceTable"]
    PR --> PJ[("data/model_prices.json")]
    A -- "on_tool_call" --> M
    M -- "agent_run, audit (con client)" --> LG["logs.py<br/>JsonLogger"]
    MW -- "request (con client), error" --> LG
    LG --> SO[("stdout<br/>una línea JSON por evento")]
    MW -- "latencia, estado" --> ME["metrics.py<br/>MetricsRecorder"]
    M -- "tokens, cost_usd" --> ME
    A -.trazas opcionales<br/>(request_id, client).-> T["tracing.py<br/>Tracer"]
    T -.-> LS[("LangSmith")]
    CK["clock.py<br/>Clock inyectado"] --> MW
    CK --> RL
    CK --> LG
    C["config.py<br/>Settings desde el entorno"] --> CO["compliance.py<br/>reglas de producción"]
    C --> M
    MW -- "X-Request-ID + JSON" --> P
```

## Publicación

```mermaid
flowchart LR
    H[Persona aprueba<br/>output/approved.txt] --> PU["scripts/publish.py<br/>git push"]
    PU --> GH[("GitHub")]
    GH --> S1[style<br/>pylint] --> S2[unit<br/>cobertura 80 %] --> S3[e2e<br/>Playwright] --> S4[docker<br/>docker build]
    S4 -- "checksPass" --> RE[("Render<br/>render.yaml, Dockerfile")]
    SEC[Secretos en<br/>Render > Environment] --> RE
    RE --> SM["smoke.py / verification production<br/>/health, 401, 200"]
```

## Piezas

| Módulo | Responsabilidad |
| --- | --- |
| `config.py` | Lee y valida las variables de entorno. Si alguna es inválida, la app no arranca |
| `compliance.py` | Con `ENVIRONMENT=production`, impide arrancar con modelo simulado, claves débiles o trazas sin ocultar |
| `security.py` | Lee `API_KEYS`, identifica al cliente por `X-API-Key` y protege todo salvo las rutas públicas |
| `ratelimit.py` | Límite de peticiones por cliente y minuto con el reloj inyectado |
| `directory.py` | Carga los empleados una vez y los consulta en solo lectura |
| `llm.py` | Interfaz `complete(messages)`, dos modelos simulados con nombre y `GatewayModel` |
| `router.py` | Regla que elige `MODEL_SMALL` o `MODEL_LARGE` y da el motivo |
| `pricing.py` | Precios por modelo desde archivo y costo en USD de una corrida |
| `agent.py` | Bucle con tope, protocolo JSON, herramientas; cuenta llamadas cacheadas |
| `main.py` | `create_app()` con dependencias inyectables; ventana, costo, 502; `/`, `/health`, `/ask`, `/metrics` |
| `middleware.py` | `request_id`, latencia, evento `request` con `client`, errores 500 y encabezado |
| `logs.py` | Log JSON en stdout, una línea por evento |
| `metrics.py` | Contadores, latencias p50/p95, tokens, costo acumulado y SLO del proceso |
| `tracing.py` | Trazas de LangSmith opcionales, apagadas por omisión, con `request_id` y `client` |
| `clock.py` | Reloj inyectable: hora UTC y tiempo monótono |
| `static/index.html` | Interfaz de RH. Campo de clave, `textContent`, historial, `request_id`, mensajes de 401 y 429 |

Fuera de `app/`: `Dockerfile`, `.dockerignore`, `.gitignore`, `render.yaml`,
`.github/workflows/ci.yml`, `scripts/smoke.py` y `doc/compliance-checklist.md`.

## Principios

- Sin estado de usuario en el servidor (ADR 0001); las métricas y el límite son del proceso
  (ADR 0005, ADR 0010).
- El modelo es intercambiable detrás de una interfaz (ADR 0002); el real va por AI Gateway y sus
  fallos son 502 (ADR 0009).
- El bucle del agente tiene tope y un protocolo estricto (ADR 0003).
- Cada petición se rastrea por su `request_id` y su cliente, sin escribir datos personales ni
  claves (ADR 0004, ADR 0010).
- Las trazas externas son opcionales y ocultan el contenido por omisión (ADR 0006); en producción
  es obligatorio (ADR 0011).
- El modelo se elige por regla, no por otro modelo (ADR 0007).
- Cada respuesta dice su costo, calculado con precios en archivo; lo cacheado no cuesta (ADR 0008).
- Negar por omisión: toda ruta pide clave salvo las públicas (ADR 0010).
- La configuración insegura no arranca en producción (ADR 0011).
- Solo se publica lo que pasa el pipeline y una persona aprobó; los secretos viven en Render
  (ADR 0012).
- Las dependencias (configuración, modelos, cliente HTTP, directorio, reloj, salida del log,
  tracer, generador de ids) se inyectan en `create_app`. Ninguna prueba toca la red.
