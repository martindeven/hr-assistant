# Evidencia: detalle por pregunta con el modelo real

- **Servicio:** `https://hr-assistant-ar3g.onrender.com` (configuración de producción: enrutamiento, ventana y
  caché de respuestas).
- **Fecha:** 2026-11-20.
- **Preguntas:** las 10 de `verification/eval_set.json`, una vez cada una, con el mismo criterio de acierto que
  `verification evaluate`.

Todas las preguntas son cortas, así que el enrutador mandó las 10 al modelo pequeño
(`@cf/meta/llama-3.1-8b-instruct-fast`, ruta `simple`).

| # | Pregunta | Resultado | Respuesta (primeros 160 caracteres) | Causa |
| --- | --- | --- | --- | --- |
| 1 | ¿Quién es EMP-0004? | OK | Jorge Díaz trabaja en Tecnología como Líder técnico desde el 10 de enero de 2022 y su estado laboral es activo. | — |
| 2 | ¿Qué puesto tiene EMP-0005? | MAL | La respuesta debe tener 'answer' o 'tool'. | El 8B mandó un JSON sin `answer` ni `tool`; el agente le devolvió el error y el modelo lo repitió como respuesta |
| 3 | ¿Quiénes trabajan en el departamento de Finanzas? | MAL | No pude completar la respuesta. Intenta con una pregunta más concreta. | Llegó al tope de pasos: el 8B no cerró el ciclo de herramientas en una lista |
| 4 | Lista el departamento de Tecnología | MAL | No pude completar la respuesta. Intenta con una pregunta más concreta. | Igual que la 3 |
| 5 | ¿Sigue activa Marta Ruiz? Su número es EMP-0003 | OK | Marta Ruiz ya no está activa en la empresa. | — |
| 6 | ¿En qué año ingresó EMP-0002? | OK | 2021 | — |
| 7 | ¿Quién es EMP-0099? | OK | No existe un empleado con id EMP-0099. | — |
| 8 | ¿Qué clima hace hoy en Monterrey? | MAL | No tengo información sobre el clima actual en Monterrey. | Rechaza bien, pero no redirige a temas de RH, que es lo que pide el criterio |
| 9 | ¿Cuántos días de vacaciones me tocan el primer año? | MAL | En el primer año de trabajo, se te otorgan 15 días de vacaciones anuales. | **Alucinación:** la política dice 12 días |
| 10 | ¿Cuántos días de home office puedo tomar por semana? | OK | De acuerdo a nuestra política de Recursos Humanos, se permite un máximo de 2 días de home office por semana, siempre y cuando se cumplan con los objetivos y met | — |

**Calidad: 5 de 10 (50 %)**, igual que la fila «Palancas + caché» de `evaluate-gateway.md`.
