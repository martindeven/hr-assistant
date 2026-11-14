# 0001. API sin estado con el historial en el cliente

Estado: aceptado

## Contexto

El HR Assistant debe escalar corriendo más copias de la API, sin coordinación entre ellas. Una
conversación tiene varias preguntas, pero cualquier copia puede atender cualquier petición.

## Decisión

- La API no guarda nada entre peticiones: ni sesiones, ni historial, ni cachés por usuario.
- El navegador guarda el historial y lo manda en `history` en cada `POST /ask`.
- Los datos de empleados se cargan una vez al crear la app y son de solo lectura.
- `create_app(settings, model, directory)` recibe sus dependencias. `app = create_app()` usa las
  de por omisión.
- `GET /health` devuelve el `pid` para ver qué copia atendió.

## Consecuencias

- Escalar es arrancar más procesos `uvicorn`. No hace falta afinidad de sesión.
- El cliente puede mandar un historial alterado. Se validan roles y tipos, y las herramientas
  son de solo lectura, así que el daño posible es bajo.
- Cada petición viaja con todo el historial. Si crece, hará falta un tope de tamaño.
