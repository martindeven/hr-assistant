# 0013. Supervisor por reglas y especialistas con herramientas propias

Estado: aceptado.

## Contexto

El asistente suma un dominio nuevo (políticas de RH) además del directorio. Un solo agente con todas las
herramientas da más superficie: el modelo podría usar cualquier herramienta para cualquier pregunta, y no se
podría dar a un cliente solo una parte. Decidir con otro modelo cuesta tokens y es menos predecible.

## Decisión

- `pipeline.py` corre siempre `guard`, `supervisor`, los especialistas elegidos y `reviewer`, en ese orden.
- `supervisor` decide por reglas (`EMP-####`, departamento, palabra clave de política), sin modelo y sin
  distinguir mayúsculas ni acentos (`text.fold`). Luego filtra por los permisos del cliente.
- Cada especialista es un `Agent` con su nombre, su prompt de sistema y **solo** sus herramientas. Una
  herramienta ajena o inexistente no se ejecuta y se audita `tool_denied`.
- El prompt de sistema viaja en la conversación; el modelo simulado lo usa para saber qué especialista lo llama.
- El enrutador elige un modelo por pregunta y lo usan todos los especialistas (ADR 0007).

## Consecuencias

- Mínimo privilegio a nivel de herramienta y de cliente, comprobable con pruebas sin modelo.
- Una pregunta mixta llama al modelo dos veces o más: más tokens y latencia.
- Una pregunta que las reglas no reconocen va a `directory`; una política nueva necesita sus `keywords`.
