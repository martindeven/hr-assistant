# Modelo de amenazas del HR Assistant

Relaciona cada riesgo del OWASP Top 10 para aplicaciones con LLM (2025) con su control en el
código, la prueba que lo comprueba y el riesgo que queda. Las reglas de arranque en producción
están en `doc/compliance-checklist.md`.

## Capas, en orden

Cada `POST /ask` cruza estas capas; si una falla, las siguientes no corren.

| # | Capa | Qué hace | Respuesta si falla | Dónde |
| --- | --- | --- | --- | --- |
| 1 | Red | HTTPS de Render; HSTS en `production`; encabezados `nosniff`, `DENY`, `no-referrer` y CSP en `GET /` | (no aplica) | Render; `app/http_layers.py`, `SecurityHeadersMiddleware` |
| 2 | Clave | `X-API-Key` comparada con `hmac.compare_digest` contra todas las claves; audita `auth_failed` | `401` | `app/security.py`, `ApiKeyAuthenticator` e `install_security` |
| 3 | Límite | `RATE_LIMIT_PER_MINUTE` por cliente y por proceso; audita `rate_limited` | `429` | `app/ratelimit.py`; `app/security.py` |
| 4 | Tamaño | Cuerpo de más de `MAX_BODY_BYTES`, sin leerlo completo | `413` | `app/http_layers.py`, `BodyLimitMiddleware` |
| 5 | Validación | Pydantic: pregunta de 1 a `MAX_QUESTION_CHARS`, historial con tope de mensajes y de largo | `422` | `app/main.py`, `_build_ask_request` |
| 6 | Guard | Inyección en pregunta e historial (`injection_blocked`); PII tapada o bloqueada (`pii_detected`) | `400` | `app/guard.py`, `app/injection.py`, `app/pii.py` |
| 7 | Permisos | `API_KEY_SCOPES`: el supervisor solo corre especialistas permitidos; `/metrics` pide `metrics` (`forbidden`) | `403` | `app/scopes.py`; `app/main.py`, `client_permissions`; `app/pipeline.py`; `app/security.py` |
| 8 | Agente | Bucle con tope `AGENT_MAX_STEPS`; protocolo JSON estricto; prompt propio por especialista | Respuesta de tope | `app/agent.py` |
| 9 | Herramientas | Solo las del especialista (`tool_denied`); argumentos validados; solo lectura | Error de herramienta al modelo | `app/agent.py`; `app/specialists.py`, `ARGUMENT_VALIDATORS` |
| 10 | Reviewer | Reglas en este orden: 1) vacía (`empty`), 2) filtración del prompt (`prompt_leak`), 3) mensaje interno del agente (`internal_message`: plantillas `INTERNAL_MESSAGE_TEMPLATES` de `app/llm.py`), 4) `EMP-####` sin evidencia (`ungrounded`); las cuatro cambian **toda** la respuesta y cortan la revisión. 5) Números de `policies` sin fundamento en la política consultada (`policy_ungrounded`): cambia **solo** la parte de `policies` y la revisión sigue. 6) PII de salida (`pii`), 7) largo. Cada reemplazo o tapado audita `output_reviewed` con `rule` | Respuesta reemplazada (toda o solo la parte de `policies`) o tapada | `app/reviewer.py`; `app/pipeline.py`, `_run_policies` |

### Reglas del reviewer sobre mensajes internos y políticas

- `internal_message`: si la respuesta trae, sin importar mayúsculas ni acentos, alguno de
  los mensajes internos del protocolo (JSON inválido, falta `answer` o `tool`, herramienta no
  permitida, argumento faltante, o el prefijo «Resultado de la herramienta: »), toda la
  respuesta se cambia por la ayuda fija (`HELP_ANSWER`). Los campos `{...}` de las plantillas
  aceptan cualquier texto.
- Consulta previa: antes de llamar al modelo de `policies`, el pipeline busca el tema en la
  pregunta con `PolicyBook.find_keyword`, ejecuta `search_policy` con ese tema y pasa el
  resultado al modelo como si él lo hubiera pedido. La consulta se audita como `tool_call`,
  se traza si hay `Tracer` y queda una sola vez, al inicio, en `tools_used`. Si la pregunta no
  menciona un tema, `policies` corre sin consulta y no hay texto de política.
- `policy_ungrounded`: la parte que escribió `policies` se parte en cláusulas (`,`, `;`, `:`,
  `.` y salto de línea). Cada número está fundamentado si alguna cláusula de la política trae
  el mismo número seguido de la misma palabra (su unidad; si el número va al final, basta el
  número) y comparte con la cláusula de la respuesta al menos una palabra con contenido (sin
  números, la unidad ni las palabras vacías de `POLICY_STOPWORDS`). Si un número no cumple, o
  si no hubo consulta, solo la parte de `policies` (su última aparición en la respuesta) se
  cambia por «Según la política: <texto de la política>», o por «No encontré una política de
  RH sobre ese tema.» si no hubo consulta. El resultado es `replaced` y se audita
  `output_reviewed` con `rule=policy_ungrounded`; después la PII y el largo se siguen
  revisando sobre la respuesta completa. Los números de la parte del directorio no se revisan
  con esta regla.

## OWASP Top 10 para aplicaciones con LLM (2025)

| Riesgo | Control en el código | Prueba que lo comprueba | Riesgo que queda |
| --- | --- | --- | --- |
| LLM01 Inyección de instrucciones | `app/injection.py` (`RULES` sobre `fold`, un solo lugar) y `app/guard.py` revisan la pregunta y **todo** el historial, también los mensajes `assistant`; `400` sin llamar al modelo. Los argumentos de herramientas se validan (`app/specialists.py`). Cada especialista solo tiene sus herramientas, de solo lectura | `tests/test_layered_security.py::test_every_injection_rule_matches_without_case_or_accents`, `::test_normal_hr_questions_are_not_taken_as_injection`; escenario «An injection hidden in an assistant message of the history is blocked»; `tests/test_multiagent.py::test_invalid_tool_arguments_return_an_error_without_reading_data` | Las reglas son frases conocidas: una paráfrasis, otro idioma o una inyección indirecta (por ejemplo, texto en `data/policies.json`) puede pasar. Lo acota que las herramientas son de solo lectura y que el reviewer revisa la salida |
| LLM02 Divulgación de información sensible | `app/pii.py` y `app/guard.py` tapan la PII de la entrada antes del enrutador, el modelo, la traza y el log (o bloquean con `block`); `app/reviewer.py` tapa la de salida; `app/audit.py` rechaza campos de texto y tapa los `args`; trazas con `HIDE_INPUTS`/`HIDE_OUTPUTS` | Escenario «An e-mail address in the question is hidden everywhere»; `tests/test_multiagent.py::test_redacted_input_never_reaches_the_model` y `::test_a_leaked_system_prompt_is_replaced_and_output_pii_is_redacted`; `tests/test_layered_security.py::test_each_kind_of_personal_data_is_replaced_by_its_label` | Los detectores son expresiones regulares: nombres propios, direcciones o formatos con otros separadores no se detectan. Los datos del directorio (nombre, puesto, departamento) sí se muestran: es la función del producto |
| LLM03 Cadena de suministro | CI con `pip-audit -r requirements.txt` (vulnerabilidades conocidas), `bandit -r app -ll` (código inseguro) y `gitleaks` (secretos en el historial), en el trabajo `security`, antes de `unit` | Trabajo `security` de `.github/workflows/ci.yml`; contrato `test_ci_has_a_security_stage` | `requirements.txt` no fija versiones: cada construcción puede traer una versión nueva que no se probó (conviene fijarlas con un archivo de bloqueo). El modelo de Workers AI y el gateway son de un tercero; una vulnerabilidad nueva puede aparecer entre corridas de CI |
| LLM04 Envenenamiento de datos y del modelo | No se entrena ni se ajusta ningún modelo; `data/employees.json` y `data/policies.json` son de solo lectura y viven en el repositorio (cambian solo por revisión de código) | Pruebas de `tests/test_multiagent.py` sobre políticas y supervisor | Un cambio malicioso en `data/` que pase la revisión llegaría a las respuestas |
| LLM05 Manejo inadecuado de la salida | La página pinta todo con `textContent` (nunca `innerHTML`) y la CSP `default-src 'self'` impide código en línea; el reviewer recorta a `MAX_ANSWER_CHARS`; la salida del modelo se interpreta solo como JSON del protocolo | `tests/test_layered_security.py::test_the_page_shows_agents_notices_and_errors_without_inline_code` y `::test_security_headers_are_on_every_response_and_hsts_only_in_production` | Un cliente propio que use la API y pinte `answer` como HTML queda fuera de este control |
| LLM06 Agencia excesiva | Herramientas de solo lectura; cada especialista solo ejecuta las suyas (`tool_denied`); argumentos validados; tope `AGENT_MAX_STEPS`; permisos por cliente en `API_KEY_SCOPES` (mínimo privilegio) | `tests/test_multiagent.py::test_a_tool_of_another_specialist_or_an_unknown_tool_is_denied`; escenario «A kiosk client only uses the policies specialist»; `tests/test_layered_security.py::test_invalid_api_key_scopes_stop_the_start_without_showing_keys` | En `development`, un cliente sin entrada en `API_KEY_SCOPES` tiene todos los permisos (en `production` no arranca) |
| LLM07 Filtración del prompt de sistema | El reviewer reemplaza toda respuesta que contenga los primeros 40 caracteres de un prompt (sin mayúsculas ni acentos); el guard bloquea «prompt del sistema», «system prompt» y «revela tu prompt». Los prompts no tienen secretos | `tests/test_multiagent.py::test_a_leaked_system_prompt_is_replaced_and_output_pii_is_redacted` | Una filtración parafraseada, traducida o de otra parte del prompt no se detecta; por eso el prompt no guarda nada secreto |
| LLM08 Debilidades de vectores y embeddings | No aplica: no hay RAG ni base vectorial. `search_policy` busca por tema o palabra clave en un archivo local | (no aplica) | Si se agrega RAG, este riesgo se tiene que revisar |
| LLM09 Desinformación | Las respuestas citan resultados de herramientas; el reviewer reemplaza una respuesta con un `EMP-####` que no viene de la pregunta ni de una herramienta («No pude verificar la respuesta con el directorio.»). Para `policies`, el pipeline consulta `search_policy` antes de llamar al modelo (auditado como `tool_call`, en `tools_used`) y la regla `policy_ungrounded` exige que cada número de esa parte aparezca en la política consultada con la misma unidad y en una cláusula que comparta una palabra con contenido; si hubo consulta y un número no está fundamentado, solo esa parte pasa a «Según la política: <texto>»; si no hubo consulta, pasa a `NO_POLICY_ANSWER` («No encontré una política de RH sobre ese tema.»). El reviewer también cambia por la ayuda una respuesta con un mensaje interno del agente (`internal_message`). Temperatura `0` por omisión | `tests/test_multiagent.py::test_an_unverified_employee_id_in_the_answer_is_replaced`; `tests/test_policy_grounding.py` (número inventado, número de la política sin su contexto, aguinaldo inventado, sin consulta, solo la parte de `policies` de una respuesta mixta, números del directorio, mensajes internos); `tests/features/policy_grounding.feature`; `verification evaluate` | Un dato inventado sin número ni id (un nombre, un requisito, un «sí» o «no») pasa. Un número que sí está en la política puede pasar en otro contexto si por casualidad coincide su unidad y una palabra de la cláusula. Hay reemplazos de más: una respuesta correcta que no comparte palabras con contenido con la política se cambia por el texto de la política (correcto, pero menos directo). Fuera de `policies` (directorio) solo se revisan los ids |
| LLM10 Consumo sin límite | Límite por cliente (`429`), tamaño del cuerpo (`413`), topes de pregunta e historial (`422`), `AGENT_MAX_STEPS`, `MODEL_MAX_TOKENS`, `MODEL_TIMEOUT_SECONDS`, reintentos con tope y costo por respuesta en `/metrics` | `tests/test_layered_security.py::test_a_body_without_content_length_is_cut_at_the_limit` y `::test_controls_run_in_order_key_then_size_then_validation_then_guard`; escenario «A huge body is refused before it is validated»; pruebas de límite de la sesión 4 | El límite es por proceso: con `WEB_CONCURRENCY` procesos el límite real se multiplica. En el peor caso una pregunta espera 3 intentos en el modelo grande y 3 en el pequeño |

## Qué datos salen del sistema y hacia dónde

| Destino | Qué sale | Cuándo | Qué no sale |
| --- | --- | --- | --- |
| Cloudflare AI Gateway y Workers AI | Prompt de sistema del especialista, historial recortado a `HISTORY_WINDOW` y pregunta, **ya tapados por el guard**; resultados de herramientas (datos del directorio y de las políticas) | Con `MODEL_PROVIDER=gateway`, en cada llamada al modelo | Claves de cliente, `request_id`, datos personales detectados (con `redact`). `CF_API_TOKEN` solo va en el encabezado de autorización |
| LangSmith | Metadatos de la corrida (`request_id`, `client`, modelo, pasos); entradas y salidas solo si `HIDE_INPUTS=false` o `HIDE_OUTPUTS=false` (prohibido en producción) | Con `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY` | Claves de cliente. Las entradas, cuando se mandan, ya van tapadas |
| Log JSON (salida estándar, Render) | Eventos `request`, `agent_run`, `audit` y `error` con conteos, tipos, ids de reglas y nombres de cliente | Siempre | Texto de la pregunta o de la respuesta, claves, valores de PII, mensajes de error |
| Navegador de la persona | Respuesta revisada, `agents`, `guardrails` (tipos, no valores), `request_id` | En cada respuesta | Prompts de sistema, resultados crudos de herramientas |
