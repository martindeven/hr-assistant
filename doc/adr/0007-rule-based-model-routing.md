# 0007. Enrutamiento por regla entre dos modelos con nombre

Estado: aceptado

## Contexto

La mayoría de las preguntas de RH son búsquedas simples que un modelo pequeño responde igual de
bien que uno grande, a una fracción del precio. Usar otro modelo para decidir costaría una llamada
más por pregunta y no sería repetible.

## Decisión

- `router.py` elige el modelo con una regla en código, en este orden: `ROUTING=false`, pregunta
  más larga que `ROUTER_MAX_CHARS`, o palabra clave («compara», «resume», «explica», «por qué»,
  «analiza», «todos», sin mayúsculas ni acentos) van a `MODEL_LARGE`; lo demás a `MODEL_SMALL`.
- Devuelve el modelo y el motivo (`route`), que se publica en la respuesta y en el log.
- La aplicación tiene un mapa `{nombre: LanguageModel}` y el agente recibe el modelo por corrida.
  Con `fake` son dos `FakeModel` que responden igual pero llevan el nombre de cada modelo.

## Consecuencias

- Decidir no cuesta nada y siempre da lo mismo para la misma pregunta.
- Ante la duda se va al grande: la regla puede pagar de más, pero rara vez pierde calidad.
- La regla es fija; si cambian las preguntas habrá que ajustarla y medir con `evaluate`.
- Las pruebas anteriores, que inyectan un solo modelo, lo usan para los dos nombres.
