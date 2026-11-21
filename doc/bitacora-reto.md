# Bitácora del reto: Sistema Multi-Agente en Producción

Registro de lo que se hizo, en orden, de los problemas que aparecieron y de lo aprendido. Las cifras vienen de la
evidencia en `doc/evidence/`.

- **Servicio:** https://hr-assistant-ar3g.onrender.com
- **Rama:** `reto/produccion` → `main` (PR #1).
- **Fecha de cierre:** 2026-11-20.

## 1. Cómo se construyó

El código lo escribió una fábrica de agentes de Claude Code en dos partes (`/reto 05` y `/reto 06`). En cada parte:

1. La fábrica propuso un plan y una persona lo aprobó.
2. Se construyó con TDD y pasó las compuertas: unit, contract, qa, e2e, evaluate y load.
3. Un revisor de estilo revisó la documentación.
4. Nada se publicó sin aprobación humana (`output/approved.txt`).

El orquestador de la fábrica nunca escribe en `product/`: solo publica lo aprobado. La evidencia y los ajustes de
CI se hicieron fuera de la fábrica y quedaron en commits aparte.

## 2. Cronología

| Paso | Qué se hizo | Commit o lugar |
| --- | --- | --- |
| 1 | Preparación: cuenta de Render, Blueprint (`render.yaml`), rama `reto/produccion`, LangSmith y Workers AI con AI Gateway | Render y GitHub |
| 2 | Parte 05 de la fábrica: supervisor, especialistas `directory` y `policies`, `guard`, `reviewer`, permisos por cliente, controles HTTP y CI con 5 etapas | `bfb6dc8`, `62713b6` |
| 3 | Despliegue en Render con `autoDeployTrigger: checksPass` | Render |
| 4 | Evidencia de seguridad en producción: inyección, PII, `403` del kiosco, `401`, encabezados, `413` y auditoría | `934e183` |
| 5 | Prueba de carga con 1 proceso; se agregó `WEB_CONCURRENCY=2` y se repitió | `934e183` |
| 6 | Se abrió el PR borrador; el CI falló en `gitleaks` | PR #1 |
| 7 | Corrección del CI: permiso `pull-requests: read` y `.gitleaksignore` | `a2d9bc0`, `a9cd565` |
| 8 | Parte 06 de la fábrica: observabilidad por agente, métricas, Prometheus, alertas, tablero, caché de respuestas y presupuesto | `dc0a96c` |
| 9 | Evaluación con el modelo real: calidad de 50 % con las palancas | `evaluate-gateway.md` |
| 10 | Diagnóstico pregunta por pregunta; se apagó el enrutamiento (`ROUTING=false`) | `evaluate-gateway-detail.md` |
| 11 | Corrección de grounding con la fábrica: calidad de 90 % | `de83091` |
| 12 | Evidencia de observabilidad: traza, logs, tablero, panel de LangSmith, Prometheus, Render y `/docs` | `23b8f64` |
| 13 | Descripción del PR, guion y video de demostración | PR #1 |

## 3. Problemas y cómo se resolvieron

### 3.1 La prueba del kiosco devolvía `401` en lugar de `403`

- **Síntoma:** la prueba de mínimo privilegio respondía `401`.
- **Causa:** el comando usaba el texto de ejemplo en lugar de la clave real del kiosco.
- **Solución:** se repitió con la clave exportada en la terminal. Dio `403 forbidden for this client`. El `401`
  quedó como evidencia aparte de la autenticación (fila 3b).

### 3.2 `HEAD /` respondía `405` y parecía no llevar encabezados

- **Síntoma:** la revisión con `curl -I` no mostraba la CSP.
- **Causa:** `curl -I` manda `HEAD`, y esa ruta solo acepta `GET`.
- **Solución:** se revisaron los encabezados con `GET`. Además se confirmó que el `405` también lleva los
  encabezados de seguridad.

### 3.3 La carga pasaba solo 30 preguntas de 200

- **Síntoma:** 30 respuestas `200` y 170 `429`.
- **Causa:** Render corría un solo proceso, y el límite es de 30 por minuto por proceso.
- **Solución:** `WEB_CONCURRENCY=2` en Render. Pasaron 59 (casi 2 x 30), sin errores `5xx`. El límite por proceso
  quedó documentado como trade-off (ADR 0015).

### 3.4 `gitleaks` falló en el PR

- **Primera causa:** en `pull_request`, gitleaks lista los commits por la API y necesita `pull-requests: read`.
  Se dio ese permiso solo a la etapa `security`.
- **Segunda causa:** dos claves de prueba falsas en el commit `bfb6dc8`, que el commit `62713b6` ya había
  cambiado. Se revisaron a mano y se ignoraron con `.gitleaksignore`, con su justificación. Ignorar un hallazgo
  de seguridad requirió aprobación explícita.
- **Resultado:** CI en verde.

### 3.5 Con el modelo real, la calidad bajó a 50 %

Con el modelo simulado las palancas ahorraban 93 % sin perder calidad. Con Workers AI:

| Configuración | Calidad | Ahorro |
| --- | --- | --- |
| Línea base (70B) | 80 % | 0 % |
| Palancas (enrutamiento al 8B + ventana) | 60 % | 80 % |
| Palancas + caché | 50 % | 87 % |

La revisión pregunta por pregunta encontró tres causas:

1. **El 8B no cierra el ciclo de herramientas** en listas (preguntas 3 y 4) y a veces manda JSON incompleto
   (pregunta 2).
2. **Alucinación de políticas.** «15 días de vacaciones» sin llamar a `search_policy`; la política dice 12. Pasaba
   **también con el 70B**: el modelo contestaba de memoria y el reviewer no lo detectaba.
3. **Fuera de tema.** El clima se rechaza bien, pero no redirige a RH.

**Solución:**

- `ROUTING=false` en Render: todas las preguntas van al 70B.
- Corrección de la fábrica (commit `de83091`):
  - `policies` llama siempre a `search_policy` antes del modelo;
  - el reviewer verifica que cada cifra esté en la política;
  - el reviewer reemplaza los mensajes internos del agente.
- **Resultado:** 9 de 10 (90 %), mejor que la línea base, con un costo de unos USD 4.50 al mes contra 9.02.

### 3.6 Falso positivo del reviewer en la pregunta mixta

- **Síntoma:** en «¿EMP-0004 tiene derecho a home office?», el reviewer reemplazó la parte de políticas
  (`policy_ungrounded`) aunque sí consultó la política.
- **Causa probable:** cuenta los dígitos de `EMP-0004` como una cifra que no está en la política.
- **Estado:** limitación conocida. La respuesta sigue siendo correcta, pero menos natural. Mejora: excluir los ids
  `EMP-####` de la verificación.

### 3.7 Datos personales en las capturas

- **Síntoma:** la barra de favoritos del navegador aparecía en la captura de `/docs`.
- **Solución:** se recortó la imagen. El guion del video pide esconder la barra antes de grabar.

## 4. Lo aprendido

### Sobre agentes en producción

- **Un modelo simulado no mide la calidad.** Las pruebas con el modelo simulado daban 100 % y ocultaban una
  alucinación que estaba en las dos versiones del modelo real. La evaluación con el modelo real es una compuerta,
  no un extra.
- **Medir pregunta por pregunta, no solo el porcentaje.** El 50 % no decía por qué. La tabla por pregunta separó
  tres causas distintas, y cada una tenía otra solución.
- **Grounding por código, no por prompt.** Pedirle al modelo que consulte la política no basta. Forzar la
  herramienta y verificar las cifras en el reviewer sí lo garantiza.
- **Los agentes de control no necesitan LLM.** `guard`, `supervisor` y `reviewer` por reglas cuestan cero, tardan
  menos de 2 ms y se prueban solos. Una pregunta bloqueada no gasta nada.
- **Los verificadores también se equivocan.** El reviewer evitó una alucinación, pero metió un falso positivo.
  Cada regla nueva necesita casos de prueba de lo que **no** debe bloquear.

### Sobre costos

- **El ahorro solo vale si la calidad se mantiene.** El 80 % de ahorro con el 8B costaba 20 puntos de calidad. El
  costo que importa es el **costo por respuesta correcta**, no por pregunta.
- **Enrutar por largo es una mala señal.** Todas las preguntas cortas iban al 8B, aunque algunas (listas) eran
  difíciles. El siguiente paso es enrutar por tipo de tarea y volver a medir.
- **La caché ahorra, pero depende de la arquitectura.** Con 2 procesos cada uno tiene su caché, y la tasa de
  acierto baja. Compartirla necesita Redis.

### Sobre observabilidad

- **Un `request_id` en todo.** La misma pregunta se reconstruye en la página, en Render > Logs y en LangSmith, y
  las latencias por agente coinciden (441 ms y 0.44 s en `directory`).
- **Las métricas deben cuadrar entre sí.** Comprobar que el costo del tablero es igual al de Prometheus, y que los
  bloqueos coinciden con la auditoría, detecta errores de instrumentación.
- **Observabilidad sin exponer datos.** Con `HIDE_INPUTS` y `HIDE_OUTPUTS`, y con `question_chars` en lugar del
  texto, se depura sin guardar la pregunta ni datos personales.
- **Las métricas por proceso confunden si no se dicen.** El tablero muestra el `pid` que respondió; sin eso, una
  caché en 0 % parecería un error.

### Sobre despliegue y seguridad

- **Leer la configuración real del proveedor.** Render no tomó `WEB_CONCURRENCY` del código: hubo que ponerlo a
  mano. Lo que no está en `render.yaml` hay que documentarlo.
- **El CI de PR tiene otros permisos que el de push.** Un job que pasa en `push` puede fallar en `pull_request`.
- **Los escáneres de secretos revisan la historia completa.** Una clave falsa corregida después sigue en un commit
  viejo. Ignorarla requiere revisarla y justificarla, nunca silenciar la regla.
- **Probar cada control con la credencial correcta.** Un `401` puede esconder si el `403` funciona.
- **Mínimo privilegio en todo:** permisos por cliente en la app, permisos por job en el CI y secretos solo en
  Render (`sync: false`).

### Sobre trabajar con una fábrica de agentes

- **Aprobación humana en cada paso de riesgo:** planes, publicación, commits, push, cambios al CI y supresión de
  hallazgos de seguridad.
- **Las correcciones vuelven a la fábrica.** El error de grounding se corrigió con un prompt a la fábrica, no a
  mano en `product/`, para que la corrección pase por las mismas compuertas.
- **Separar la evidencia del código.** La evidencia y los ajustes de CI fueron commits propios, con mensajes que
  dicen por qué.

## 5. Pendientes

- Excluir los ids `EMP-####` de la verificación de cifras del reviewer.
- Redirigir a RH las preguntas fuera de tema.
- Agregar `request_id` a la respuesta `413`.
- Poner `MODEL_TIMEOUT_SECONDS` en `render.yaml`.
- Enrutar por tipo de tarea y volver a medir con el modelo real.
- Estado compartido (límite, caché, presupuesto y métricas) en Redis.
