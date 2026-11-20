# Lista de compliance

Con `ENVIRONMENT=production`, `load_settings` (`app/config.py`) llama a
`check_production` (`app/compliance.py`) antes de devolver la configuración. Si alguna regla falla,
lanza un solo `ConfigError` con todas las fallas juntas; como `app = create_app()` corre al
importar `app.main`, el proceso termina con código distinto de 0 y el servicio no arranca. En
`development` estas reglas no se aplican. Los mensajes nombran variables, clientes y largos, nunca
el valor de una clave.

| Regla | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| El modelo no puede ser `fake` (`MODEL_PROVIDER`) | `app/compliance.py`, `_model_rule` | `tests/test_compliance.py::test_each_unsafe_production_setting_refuses_to_start[fake_model]` y el escenario «Production refuses to start with the fake model» de `tests/features/production.feature` |
| `API_KEYS` no puede estar vacía | `app/compliance.py`, `_api_keys_rule` | `tests/test_compliance.py::test_each_unsafe_production_setting_refuses_to_start[no_api_keys]` |
| Cada clave tiene 16 caracteres o más (el error nombra al cliente y el largo, no la clave) | `app/compliance.py`, `_api_keys_rule` (`MIN_PRODUCTION_KEY_LENGTH`) | `tests/test_compliance.py::test_each_unsafe_production_setting_refuses_to_start[key_shorter_than_16]` |
| Con trazas encendidas, `HIDE_INPUTS=true` | `app/compliance.py`, `_tracing_rule` | `tests/test_compliance.py::test_each_unsafe_production_setting_refuses_to_start[traces_show_inputs]` |
| Con trazas encendidas, `HIDE_OUTPUTS=true` | `app/compliance.py`, `_tracing_rule` | `tests/test_compliance.py::test_each_unsafe_production_setting_refuses_to_start[traces_show_outputs]` |
| Todas las fallas se informan juntas y sin ninguna clave | `app/compliance.py`, `check_production` | `tests/test_compliance.py::test_all_failing_rules_are_reported_together_without_any_key` |
| Las reglas solo se aplican en producción; `ENVIRONMENT` solo acepta `development` o `production` | `app/compliance.py`, `check_production`; `app/config.py`, `_environment` | `tests/test_compliance.py::test_compliance_applies_only_in_production` y las pruebas de configuración de `tests/` |
| `PII_INPUT_MODE` no puede ser `off` | `app/compliance.py`, `_pii_input_rule` | `tests/test_layered_security.py::test_production_refuses_unsafe_guards_and_clients_without_scopes`; contrato `test_production_refuses_to_start_without_pii_protection` |
| `INJECTION_GUARD` debe valer `true` | `app/compliance.py`, `_injection_guard_rule` | `tests/test_layered_security.py::test_production_refuses_unsafe_guards_and_clients_without_scopes`; contrato `test_production_refuses_to_start_without_the_injection_guard` |
| Cada cliente de `API_KEYS` tiene entrada en `API_KEY_SCOPES` (el error nombra al cliente, nunca su clave) | `app/compliance.py`, `_scopes_rule` | `tests/test_layered_security.py::test_production_refuses_unsafe_guards_and_clients_without_scopes`; contrato `test_production_refuses_a_client_without_scopes` |

## Controles que acompañan a las reglas

Valen en cualquier ambiente; no impiden el arranque salvo `API_KEYS` o `API_KEY_SCOPES` mal
formadas.

| Control | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| `API_KEYS` mal formada detiene el arranque; el error dice entrada y largo, sin valor ni excepción encadenada | `app/security.py`, `parse_api_keys` | `tests/test_security.py::test_malformed_api_keys_name_the_entry_and_length_but_never_the_value` |
| Toda ruta fuera de `/`, `/health`, `/ready`, `/static/app.js`, `/static/app.css`, `/docs`, `/docs/oauth2-redirect` y `/openapi.json` pide `X-API-Key`; `401` antes que `422` | `app/security.py`, `install_security` y `PUBLIC_PATHS` | `tests/test_security.py` (ruta desconocida y cuerpo inválido sin clave) y `tests/features/production.feature` |
| Las claves se comparan con `hmac.compare_digest`, contra todas, sin salida temprana | `app/security.py`, `ApiKeyAuthenticator.identify` | `tests/test_security.py::test_authenticator_compares_every_key_with_compare_digest` |
| Límite de peticiones por cliente con `429` y `Retry-After` | `app/ratelimit.py`, `RateLimiter.check`; `app/security.py`, `install_security` | `tests/test_ratelimit.py` y los escenarios de límite de `tests/features/production.feature` |
| La clave no sale en `repr(settings)`, en el log ni en la traza; el log y la traza llevan solo el cliente | `app/config.py` (`api_keys` con `repr=False`); `app/middleware.py`, `app/main.py` y `app/tracing.py` (campo `client`) | Escenario «A valid API key answers and the log names the client but never the key» y `tests/test_tracing.py::test_trace_metadata_names_the_client_that_asked` |
| La página guarda la clave solo en `sessionStorage`, nunca en el HTML servido | `app/static/app.js` (`API_KEY_STORAGE`) | Pruebas de la página en `tests/` y las pruebas de punta a punta de `e2e/` |

## Controles de la parte 1 del reto

Valen en cualquier ambiente. En producción, las tres reglas nuevas de arriba impiden apagar los
dos primeros y dejar a un cliente sin permisos declarados. El modelo de amenazas completo está
en `doc/security.md`.

| Control | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| Datos personales (correo, teléfono, CURP, RFC, NSS, tarjeta con Luhn, CLABE) se tapan con su etiqueta en la entrada (`redact`) o la bloquean con `400` (`block`), antes del enrutador, el modelo, la traza y el log | `app/pii.py` (`PATTERNS`, `redact`); `app/guard.py`, `Guard._check_pii` | `tests/test_layered_security.py::test_each_kind_of_personal_data_is_replaced_by_its_label` y `::test_ordinary_numbers_ids_and_dates_are_not_personal_data`; escenarios «An e-mail address in the question is hidden everywhere» y «Personal data can be blocked instead of hidden»; `tests/test_multiagent.py::test_redacted_input_never_reaches_the_model` |
| Inyección de instrucciones en la pregunta o en cualquier mensaje del historial responde `400` sin llamar al modelo; se audita solo el id de la regla | `app/injection.py` (`RULES`, `match`); `app/guard.py`, `Guard._check_injection` | `tests/test_layered_security.py::test_every_injection_rule_matches_without_case_or_accents` y `::test_normal_hr_questions_are_not_taken_as_injection`; escenario «An injection hidden in an assistant message of the history is blocked» |
| Argumentos de herramientas validados (`employee_id` `^EMP-\d{4}$`; `department` y `topic` de 60 caracteres como máximo) sin leer datos | `app/specialists.py`, `ARGUMENT_VALIDATORS`; `app/agent.py`, `Agent._execute` | `tests/test_multiagent.py::test_invalid_tool_arguments_return_an_error_without_reading_data` |
| Cada especialista solo ejecuta sus herramientas; las ajenas o inexistentes se niegan y se audita `tool_denied` | `app/agent.py`, `Agent._call_tool` | `tests/test_multiagent.py::test_a_tool_of_another_specialist_or_an_unknown_tool_is_denied` |
| El `reviewer` reemplaza respuestas vacías, con filtración del prompt o con `EMP-####` sin evidencia; tapa la PII de salida y recorta a `MAX_ANSWER_CHARS` | `app/reviewer.py`, `review` | `tests/test_multiagent.py::test_an_unverified_employee_id_in_the_answer_is_replaced`, `::test_a_leaked_system_prompt_is_replaced_and_output_pii_is_redacted` y `::test_empty_answers_get_the_help_and_long_answers_are_cut` |
| Permisos por cliente (`API_KEY_SCOPES`): el supervisor solo corre los especialistas permitidos (`403` si ninguno) y `/metrics` pide `metrics`; mal formada no arranca y el error no muestra claves | `app/scopes.py` (`parse_api_key_scopes`, `permissions_for`); `app/main.py`, `client_permissions`; `app/security.py`, `install_security` | `tests/test_layered_security.py::test_invalid_api_key_scopes_stop_the_start_without_showing_keys`; escenario «A kiosk client only uses the policies specialist»; contrato `test_a_client_only_uses_the_agents_it_is_allowed` |
| Encabezados de seguridad en toda respuesta, CSP en `GET /`, `no-store` en `/ask`, HSTS solo en producción | `app/http_layers.py`, `SecurityHeadersMiddleware` | `tests/test_layered_security.py::test_security_headers_are_on_every_response_and_hsts_only_in_production` |
| Cuerpo de más de `MAX_BODY_BYTES` responde `413` sin leerse completo (con y sin `Content-Length`) | `app/http_layers.py`, `BodyLimitMiddleware` | `tests/test_layered_security.py::test_a_body_without_content_length_is_cut_at_the_limit`; escenario «A huge body is refused before it is validated» |
| Orden de los controles: clave, límite, tamaño, validación, guard y permisos | `app/main.py`, `create_app` (orden de instalación de middlewares) | `tests/test_layered_security.py::test_controls_run_in_order_key_then_size_then_validation_then_guard` |
| Auditoría de `auth_failed`, `rate_limited`, `forbidden`, `pii_detected`, `injection_blocked`, `output_reviewed`, `tool_call`, `tool_denied` y `fallback`, sin texto ni claves | `app/audit.py` (`Auditor`, `FORBIDDEN_FIELDS`, `audit_args` tapa y recorta); `app/security.py`; `app/guard.py`; `app/reviewer.py`; `app/pipeline.py` | Escenarios de `tests/features/security.feature` y `tests/test_multiagent.py` |
| La página muestra «Se ocultaron datos sensibles», «La pregunta no se puede procesar» (`400`) y «No tienes permiso para esa consulta» (`403`), todo con `textContent` y sin código en línea | `app/static/app.js`, `app/static/index.html` | `tests/test_layered_security.py::test_the_page_shows_agents_notices_and_errors_without_inline_code` y `e2e/` |
