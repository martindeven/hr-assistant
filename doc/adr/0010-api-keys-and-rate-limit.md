# 0010. Clave por cliente y límite de peticiones en un middleware

Estado: aceptado.

## Contexto

El producto se publica en Internet. Hay que saber qué cliente pregunta, impedir que alguien sin
clave use el modelo (que cuesta) y evitar que un cliente con clave lo sature. No hay base de datos
ni usuarios: la API no guarda estado de usuario (ADR 0001).

## Decisión

- `API_KEYS` (`cliente:clave,...`) se lee al arrancar. Si está mal formada, el producto no arranca;
  el error dice el número de entrada y su largo, nunca el valor.
- Un middleware de `security.py` niega por omisión: solo `/`, `/health`, `/docs` y `/openapi.json`
  son públicas. Las demás piden `X-API-Key`; sin clave válida, `401 {"detail": "invalid or missing
  API key"}`, antes de validar el cuerpo.
- Se comparan todas las claves con `hmac.compare_digest`, sin salir antes.
- El nombre del cliente (no la clave) va en `request.state`, en el log y en la traza.
- `ratelimit.py`: ventana deslizante de 60 s por cliente, en memoria, con el reloj inyectado. Al
  pasar `RATE_LIMIT_PER_MINUTE`, `429` con `Retry-After`. Solo cuentan las peticiones aceptadas.
- El middleware de seguridad va dentro del de observabilidad: todo 401/429 lleva `request_id`.

## Consecuencias

- Una ruta nueva queda protegida sin acordarse de ello.
- El límite es por proceso, como las métricas (ADR 0005): con varios procesos se multiplica. En
  Render free hay una instancia; si se escala, hará falta un almacén compartido.
- Cambiar o revocar una clave requiere redesplegar con otra `API_KEYS`.
- Las pruebas de las sesiones 1 a 3 necesitan mandar una clave: lo resuelven los fixtures.
