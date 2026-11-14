# 0002. Interfaz única del modelo y modelo simulado

Estado: aceptado. Ampliado por el ADR 0007 (`build_models`) y el ADR 0009 (proveedor `gateway`).

## Contexto

El agente no debe depender de un proveedor concreto. En esta sesión no hay red ni presupuesto, y
las pruebas deben ser deterministas.

## Decisión

- `llm.py` define `LanguageModel`, un `Protocol` con `complete(messages) -> Completion`.
  `Completion` tiene `content` (el texto JSON), `input_tokens`, `output_tokens` y `cached`
  (`True` si el gateway la sirvió de su caché; por omisión `False`).
- `FakeModel` implementa las 4 reglas de la spec. Recibe por el constructor la lista de
  departamentos, así que no lee archivos. Cuenta los tokens por palabras.
- `build_models(settings, directory)` elige la implementación según `MODEL_PROVIDER` y devuelve
  `{nombre: modelo}` para `MODEL_SMALL` y `MODEL_LARGE` (ADR 0007). Acepta `fake` y `gateway`
  (ADR 0009); cualquier otro valor es un error de configuración. `build_model(settings, directory)`
  se conserva por compatibilidad y devuelve el modelo de `MODEL_LARGE`.

## Consecuencias

- Para agregar un modelo real hace falta una clase nueva y una rama en `build_models`, sin tocar
  `agent.py`.
- Las pruebas pueden inyectar modelos guionados para forzar los casos de error del protocolo.
- El conteo de tokens del modelo simulado es aproximado: no sirve para estimar costos reales.
