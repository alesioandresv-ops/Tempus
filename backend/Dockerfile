# syntax=docker/dockerfile:1

FROM python:3.13-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias de Python.
#
# **Sin `[dev]` a proposito.** El extra `dev` trae pytest, mypy, ruff y Coverage:
# casi 300 MB de imagen que en produccion no se usan, y un attack surface que no
# hace falta. Se instalan primero desde el `pyproject.toml` para que la capa de
# dependencias se cachee: si no, cada cambio de codigo reinstala todo.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copiar codigo
COPY . .

# Usuario sin privilegios. El contenedor por defecto corre como root, asi que una
# RCE en la app--o un dependencia comprometida-- hereda root. El usuario `appuser`
# existe solo para que `chown` tenga un dueno con nombre y el `USER` sea legible.
#
# El `chmod +x` no es cosmetico: Git en Windows no guarda el bit de ejecucion, asi
# que el `ENTRYPOINT` llega al build sin permiso y el contenedor muere con
# "permission denied" antes de levantar nada.
RUN chmod +x /app/docker-entrypoint.sh \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# La sonda usa `/healthz`, que esta **fuera** del prefijo de version a proposito: es
# donde las plataformas de hosting la buscan. `--proxy-headers` va activo por
# defecto; `--forwarded-allow-ips` se pasa desde el despliegue y **no** puede
# quedar en "confia en cualquiera": ver la seccion "Rate limiting y proxies" del
# README. Sin el, `client.host` es el proxy y todos los usuarios caen en un mismo
# cubo de 5/min de login.
ARG FORWARDED_ALLOW_IPS=""
ENV FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS}"

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# El entrypoint corre `alembic upgrade head` antes de levantar el servidor. Es
# idempotente--ya aplicado, no hace nada-- asi que reiniciar un pod no rompe nada,
# y hace que no exista el despliegue en el que la app arranca contra un esquema viejo.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]