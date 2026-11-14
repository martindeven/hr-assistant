# Imagen del HR Assistant (spec/04-production.md, punto 5; doc/adr/0012).
FROM python:3.12-slim

# Salida sin búfer para que los logs JSON lleguen enseguida; sin archivos .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/hr-assistant

# Primero las dependencias, para aprovechar la caché de capas de Docker
COPY requirements.txt .
RUN pip install -r requirements.txt

# Solo lo que el producto necesita para correr; el resto lo deja fuera .dockerignore
COPY app/ app/
COPY data/ data/

# Usuario sin privilegios: el proceso no corre como root
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Render pone PORT; en local se usa 8000. exec deja a uvicorn como PID 1 para recibir las señales
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
