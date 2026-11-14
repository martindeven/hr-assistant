"""Prueba de humo contra el HR Assistant publicado (spec/04-production.md, punto 8).

Uso: ``SMOKE_API_KEY=<clave> python scripts/smoke.py https://<servicio>.onrender.com``

La clave se lee de la variable ``SMOKE_API_KEY`` y nunca de un argumento, porque
quedaría en el historial de la terminal. Nunca se imprime.
"""

import os
import sys

import httpx

# Render despierta un servicio dormido (plan free) en hasta un minuto
TIMEOUT_SECONDS = 90
QUESTION = {"question": "¿Quién es EMP-0004?"}
USAGE = "Uso: SMOKE_API_KEY=<clave> python scripts/smoke.py <url>"


def run_checks(url: str, key: str, client: httpx.Client) -> bool:
    """Comprueba ``/health``, ``/ask`` sin clave y ``/ask`` con clave.

    Imprime ``[OK]`` o ``[FAIL]`` por comprobación, con el código recibido: así se
    ve la causa de una falla (401: la clave no coincide; 502: el modelo no responde).

    :param url: URL base del servicio, sin barra final
    :type url: str
    :param key: Clave de cliente para ``X-API-Key``
    :type key: str
    :param client: Cliente HTTP con el que se hacen las peticiones
    :type client: httpx.Client
    :return: ``True`` si las tres comprobaciones pasan
    :rtype: bool
    """
    checks = [
        ("GET /health", 200, lambda: client.get(f"{url}/health")),
        ("POST /ask sin clave", 401, lambda: client.post(f"{url}/ask", json=QUESTION)),
        ("POST /ask con clave", 200, lambda: client.post(
            f"{url}/ask", json=QUESTION, headers={"X-API-Key": key})),
    ]
    passed = True
    for name, expected, request in checks:
        try:
            status_code = request().status_code
        except httpx.HTTPError as error:
            # Solo el tipo de error: el mensaje podría traer datos de la petición
            print(f"  [FAIL] {name} -> {type(error).__name__} (esperado {expected})")
            passed = False
            continue
        status = "OK" if status_code == expected else "FAIL"
        print(f"  [{status}] {name} -> {status_code} (esperado {expected})")
        passed = passed and status_code == expected
    return passed


def main(args: list[str]) -> int:
    """Corre la prueba de humo.

    :param args: Argumentos de la línea de comandos, sin el nombre del programa
    :type args: list[str]
    :return: 0 si todo pasa, 1 si algo falla y 2 si falta la URL o la clave
    :rtype: int
    """
    key = os.environ.get("SMOKE_API_KEY", "")
    if len(args) != 1 or not key:
        print(USAGE)
        return 2
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        passed = run_checks(args[0].rstrip("/"), key, client)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
