# 0016. Observabilidad por agente desde un solo registro de pasos

Estado: aceptado.

## Contexto

Con cinco agentes por pregunta, el log, `/metrics` y LangSmith solo ven la petición completa: no se sabe qué
agente tardó, falló o bloqueó. La parte 2 del reto pide trazas por agente, el evento `agent_step`, métricas por
agente, cliente y modelo, un endpoint Prometheus y alertas. Si cada salida midiera por su cuenta, los números no
coincidirían entre sí.

## Decisión

- Un `StepRecorder` por petición envuelve cada agente (`guard`, `supervisor`, especialistas, `reviewer`): mide
  la latencia con el reloj inyectado, abre su nodo hijo del nodo raíz `ask` y fija el `outcome` (`ok`,
  `blocked`, `forbidden`, `skipped`, `error`) según la excepción. De ese registro salen `agent_step`, `by_agent`,
  `hr_agent_runs_total` y los metadatos de la traza.
- Los contadores de barandales salen del `Auditor` (un observador por evento), la misma fuente que el audit.
- `MetricsRecorder` guarda conteos acumulados (para Prometheus) y ventanas de 1000 (para p95 y alertas). Las
  alertas son una función pura y el evento `alert` sale solo cuando cambia el estado.
- Prometheus se escribe a mano en formato de texto 0.0.4, sin librerías, con la etiqueta `pid` y etiquetas de
  cardinalidad acotada (nunca `client` ni `request_id`).
- Con las trazas apagadas, el registro sigue alimentando log y métricas, pero no se llama a `langsmith`.

## Consecuencias

- Log, métricas y trazas coinciden porque salen del mismo paso.
- Todo sigue siendo del proceso (ADR 0005): para un total hay que sumar por `pid` en Prometheus.
- Con respaldo, un especialista deja dos pasos (`error` y `ok`).
- Un agente nuevo solo tiene que correr dentro de `steps.step(nombre)`.
