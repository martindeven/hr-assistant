"""Prueba de carga contra el HR Assistant (spec/05-multiagent-security.md, punto 5).

Uso::

    LOAD_API_KEY=<clave> python scripts/load_test.py <url> --requests 80 --concurrency 16

Manda ``N`` preguntas a ``POST /ask`` con ``C`` peticiones al mismo tiempo e imprime
una tabla Markdown con los códigos de estado, la latencia y los procesos (``pid`` de
``/health``) que respondieron. Sirve como evidencia de concurrencia contra Render.

La clave se lee de ``LOAD_API_KEY`` y nunca de un argumento, porque quedaría en el
historial de la terminal. Nunca se imprime.
"""

import argparse
import asyncio
import math
import os
import sys
import time
from collections import Counter

import httpx

# Render despierta un servicio dormido (plan free) en hasta un minuto
TIMEOUT_SECONDS = 90
# Consultas a /health para ver cuántos procesos distintos responden
HEALTH_PROBES = 50
QUESTIONS = (
    "¿Quién es EMP-0004?",
    "¿Quiénes trabajan en Finanzas?",
    "¿Cuántos días de vacaciones me tocan el primer año?",
    "¿Qué puesto tiene EMP-0005?",
)
# Código que se anota cuando la petición no llegó a tener respuesta (red, tiempo)
NO_RESPONSE = "sin respuesta"
USAGE = (
    "Uso: LOAD_API_KEY=<clave> python scripts/load_test.py <url> "
    "--requests N --concurrency C"
)


def percentile(values: list[float], fraction: float) -> float:
    """Percentil por rango más cercano.

    :param values: Latencias en milisegundos
    :type values: list[float]
    :param fraction: Percentil buscado entre 0 y 1 (por ejemplo 0.95)
    :type fraction: float
    :return: El valor del percentil, o 0 si no hay valores
    :rtype: float
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


async def ask(client: httpx.AsyncClient, gate: asyncio.Semaphore,
              number: int) -> tuple[object, float]:
    """Manda una pregunta respetando el tope de peticiones simultáneas.

    :param client: Cliente HTTP con la URL base y la clave
    :type client: httpx.AsyncClient
    :param gate: Semáforo que limita cuántas peticiones van al mismo tiempo
    :type gate: asyncio.Semaphore
    :param number: Número de la pregunta; elige el texto de ``QUESTIONS``
    :type number: int
    :return: Código de estado (o ``NO_RESPONSE``) y latencia en milisegundos
    :rtype: tuple[object, float]
    """
    async with gate:
        started = time.perf_counter()
        try:
            response = await client.post(
                "/ask", json={"question": QUESTIONS[number % len(QUESTIONS)]}
            )
            status = response.status_code
        except httpx.HTTPError:
            # Solo se cuenta: el mensaje del error podría traer datos de la petición
            status = NO_RESPONSE
        return status, (time.perf_counter() - started) * 1000


async def probe_pid(client: httpx.AsyncClient, gate: asyncio.Semaphore) -> object:
    """Consulta ``/health`` y devuelve el ``pid`` del proceso que respondió.

    :param client: Cliente HTTP con la URL base
    :type client: httpx.AsyncClient
    :param gate: Semáforo que limita cuántas peticiones van al mismo tiempo
    :type gate: asyncio.Semaphore
    :return: El ``pid``, o ``None`` si no hubo respuesta válida
    :rtype: object
    """
    async with gate:
        try:
            return (await client.get("/health")).json().get("pid")
        except (httpx.HTTPError, ValueError):
            return None


async def measure(url: str, key: str, requests: int, concurrency: int) -> dict:
    """Corre la carga y junta códigos, latencias y procesos.

    :param url: URL base del servicio, sin barra final
    :type url: str
    :param key: Clave de cliente para ``X-API-Key``
    :type key: str
    :param requests: Cuántas preguntas mandar
    :type requests: int
    :param concurrency: Cuántas peticiones al mismo tiempo
    :type concurrency: int
    :return: Códigos por estado, latencias p50, p95 y máxima y los ``pid`` vistos
    :rtype: dict
    """
    gate = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=url, headers={"X-API-Key": key}, timeout=TIMEOUT_SECONDS
    ) as client:
        results = await asyncio.gather(*(ask(client, gate, n) for n in range(requests)))
        pids = await asyncio.gather(
            *(probe_pid(client, gate) for _ in range(HEALTH_PROBES))
        )
    latencies = [latency for _, latency in results]
    return {
        "total": len(results),
        "statuses": Counter(str(status) for status, _ in results),
        "p50": percentile(latencies, 0.50),
        "p95": percentile(latencies, 0.95),
        "max": max(latencies, default=0.0),
        "pids": sorted({pid for pid in pids if pid is not None}),
    }


def render_table(result: dict, concurrency: int) -> str:
    """Arma la tabla Markdown con el resultado.

    :param result: Lo que devolvió :func:`measure`
    :type result: dict
    :param concurrency: Peticiones simultáneas usadas, para dejarlo en la tabla
    :type concurrency: int
    :return: La tabla en Markdown
    :rtype: str
    """
    rows = [
        ("Peticiones totales", str(result["total"])),
        ("Concurrencia", str(concurrency)),
    ]
    for status in sorted(result["statuses"]):
        rows.append((f"Código {status}", str(result["statuses"][status])))
    rows += [
        ("Respuestas 429", str(result["statuses"].get("429", 0))),
        ("Latencia p50 (ms)", f"{result['p50']:.0f}"),
        ("Latencia p95 (ms)", f"{result['p95']:.0f}"),
        ("Latencia máxima (ms)", f"{result['max']:.0f}"),
        ("Procesos distintos en /health", str(len(result["pids"]))),
        ("pid", ", ".join(str(pid) for pid in result["pids"]) or "-"),
    ]
    lines = ["| Métrica | Valor |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    return "\n".join(lines)


def positive_int(text: str) -> int:
    """Convierte un argumento en entero mayor que cero.

    :param text: Texto del argumento
    :type text: str
    :return: El entero
    :rtype: int
    :raises argparse.ArgumentTypeError: Si no es un entero mayor que cero
    """
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("debe ser un entero") from error
    if value < 1:
        raise argparse.ArgumentTypeError("debe ser mayor que cero")
    return value


def main(args: list[str]) -> int:
    """Corre la prueba de carga.

    :param args: Argumentos de la línea de comandos, sin el nombre del programa
    :type args: list[str]
    :return: 0 si todas las preguntas respondieron 200, 1 si alguna no y 2 si
        falta la clave
    :rtype: int
    """
    parser = argparse.ArgumentParser(description="Prueba de carga del HR Assistant")
    parser.add_argument("url", help="URL base del servicio")
    parser.add_argument("--requests", type=positive_int, default=80,
                        help="Cuántas preguntas mandar (por omisión 80)")
    parser.add_argument("--concurrency", type=positive_int, default=16,
                        help="Peticiones al mismo tiempo (por omisión 16)")
    options = parser.parse_args(args)
    key = os.environ.get("LOAD_API_KEY", "")
    if not key:
        print(USAGE)
        return 2
    result = asyncio.run(
        measure(options.url.rstrip("/"), key, options.requests, options.concurrency)
    )
    print(render_table(result, options.concurrency))
    return 0 if result["statuses"].get("200", 0) == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
