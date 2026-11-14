# 0003. Un solo agente con bucle de tope y protocolo JSON

Estado: aceptado

## Contexto

El agente decide en cada vuelta si usa una herramienta o responde. Sin un límite, un modelo que
se equivoca puede repetir sin fin. Además, el contrato fija valores exactos de `steps`.

## Decisión

- Un solo agente con dos herramientas de solo lectura: `get_employee` y `search_by_department`.
- Cada vuelta es una llamada al modelo, y `steps` cuenta esas llamadas. El máximo es
  `AGENT_MAX_STEPS` (por omisión 3, siempre mayor que 0).
- El modelo responde un objeto JSON: `{"tool", "args"}` o `{"answer"}`. El resultado de una
  herramienta vuelve como mensaje `user` que empieza con `Resultado de la herramienta: `,
  serializado con `ensure_ascii=False`.
- Un JSON inválido, una herramienta desconocida o `args` incorrectos vuelven al modelo como
  resultado de error, sin lanzar excepciones. Si se llega al tope, la respuesta es exactamente:
  `No pude completar la respuesta. Intenta con una pregunta más concreta.`

## Consecuencias

- El costo y la latencia de cada pregunta tienen un límite conocido.
- Las preguntas que necesitan más vueltas que el tope reciben el mensaje de tope. El tope se
  ajusta por configuración.
- Agregar una herramienta es registrarla por nombre en el agente.
