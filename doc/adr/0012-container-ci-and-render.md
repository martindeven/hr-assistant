# 0012. Contenedor, pipeline de CI visible y Render solo si el pipeline pasa

Estado: aceptado.

## Contexto

Hay que publicar el producto sin que un agente ni una persona lo hagan a mano, que los ingenieros
vean por qué se publicó o no, y sin poner secretos en el repositorio.

## Decisión

- Imagen `python:3.12-slim`, usuario sin privilegios, `PYTHONUNBUFFERED=1` y
  `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}` con `exec`. `.dockerignore` deja fuera
  `.env`, pruebas y cachés.
- GitHub Actions en cada push y pull request con cuatro trabajos encadenados por `needs`: `style`
  (pylint, 88 columnas), `unit` (cobertura de 80 % o más), `e2e` (Playwright con el modelo
  simulado y una clave de ejemplo) y `docker` (`docker build`). pylint y Playwright se instalan en
  su trabajo, no en `requirements.txt`.
- `render.yaml` (Blueprint): un servicio web con el nombre del repositorio, `runtime: docker`,
  `plan: free`, `healthCheckPath: /health` y `autoDeployTrigger: checksPass`. Variables fijas:
  `ENVIRONMENT=production`, `MODEL_PROVIDER=gateway`, `HIDE_INPUTS=true`, `HIDE_OUTPUTS=true`.
  Secretos con `sync: false`.
- Una persona aprueba antes del push (`output/approved.txt`); después, `scripts/smoke.py` (y
  `verification production`) comprueba el servicio publicado con la clave de `SMOKE_API_KEY`.

## Consecuencias

- Un push que rompe estilo, pruebas o imagen no llega a Render.
- Los secretos viven solo en Render; la persona los escribe allí, nunca en el chat.
- El plan free duerme el servicio: la primera petición tarda y el humo espera hasta 90 s.
- La imagen incluye las librerías de prueba de `requirements.txt`; separarlas queda pendiente.
