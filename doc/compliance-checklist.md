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

## Controles que acompañan a las reglas

Valen en cualquier ambiente; no impiden el arranque salvo `API_KEYS` mal formada.

| Control | Dónde está en el código | Cómo se comprueba |
| --- | --- | --- |
| `API_KEYS` mal formada detiene el arranque; el error dice entrada y largo, sin valor ni excepción encadenada | `app/security.py`, `parse_api_keys` | `tests/test_security.py::test_malformed_api_keys_name_the_entry_and_length_but_never_the_value` |
| Toda ruta fuera de `/`, `/health`, `/docs`, `/docs/oauth2-redirect` y `/openapi.json` pide `X-API-Key`; `401` antes que `422` | `app/security.py`, `install_security` y `PUBLIC_PATHS` | `tests/test_security.py` (ruta desconocida y cuerpo inválido sin clave) y `tests/features/production.feature` |
| Las claves se comparan con `hmac.compare_digest`, contra todas, sin salida temprana | `app/security.py`, `ApiKeyAuthenticator.identify` | `tests/test_security.py::test_authenticator_compares_every_key_with_compare_digest` |
| Límite de peticiones por cliente con `429` y `Retry-After` | `app/ratelimit.py`, `RateLimiter.check`; `app/security.py`, `install_security` | `tests/test_ratelimit.py` y los escenarios de límite de `tests/features/production.feature` |
| La clave no sale en `repr(settings)`, en el log ni en la traza; el log y la traza llevan solo el cliente | `app/config.py` (`api_keys` con `repr=False`); `app/middleware.py`, `app/main.py` y `app/tracing.py` (campo `client`) | Escenario «A valid API key answers and the log names the client but never the key» y `tests/test_tracing.py::test_trace_metadata_names_the_client_that_asked` |
| La página guarda la clave solo en `sessionStorage`, nunca en el HTML servido | `app/static/index.html` | Pruebas de la página en `tests/` y las pruebas de punta a punta de `e2e/` |
