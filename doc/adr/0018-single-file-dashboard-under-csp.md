# 0018. Tablero de un solo archivo bajo la misma CSP que la página

Estado: aceptado.

## Contexto

La spec pide `GET /dashboard` con HTML, CSS y JavaScript en el mismo archivo y, a la vez, la misma
`Content-Security-Policy` que `/` (`default-src 'self'`, ADR 0014). Esa CSP bloquea el `<script>` y el `<style>`
en línea, así que el tablero no correría. Usar `'unsafe-inline'` abriría la puerta a XSS en las dos páginas.

## Decisión

- `static/dashboard.html` trae exactamente un `<style>` y un `<script>`, sin atributos `style=` ni manejadores en
  línea, y escribe todo con `textContent`.
- Al arrancar, `create_app` calcula el sha256 de esos dos bloques y arma una sola CSP: `default-src 'self';
  script-src 'self' 'sha256-...'; style-src 'self' 'sha256-...'; frame-ancestors 'none'`. Se manda igual en `GET /`
  y en `GET /dashboard`. Si el archivo no tiene esos dos bloques, la app no arranca.
- La página es pública; sus datos (`/metrics`) piden clave y permiso `metrics`. La clave se lee de la misma
  entrada de `sessionStorage` que usa `/`.

## Consecuencias

- Solo corre el código exacto del archivo: cualquier cambio en el tablero cambia el hash solo, sin tocar
  configuración.
- `index.html` sigue sin código en línea; su CSP gana dos hashes que no le afectan.
- Un error de espacios entre el archivo y el hash deja la página en blanco: lo atrapan la prueba del hash y la e2e.
