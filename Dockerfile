# syntax=docker/dockerfile:1

# Imagem do backend (Django + Ninja Extra). Dois alvos:
#   runtime     desenvolvimento (compose.yaml): inclui pytest e ruff; o comando padrão é o runserver.
#   production  produção/homologação: sem dependências de dev, gunicorn e estáticos coletados.
# Sem --target, o build gera "production" (último estágio).
ARG PYTHON_VERSION=3.13.18

FROM python:${PYTHON_VERSION}-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# WITH_DEV=1 inclui o grupo "dev" (pytest e ruff); use 0 para uma imagem só com runtime.
ARG WITH_DEV=1
# COPY em vez de --mount=type=bind: o Railway só aceita cache mounts e rejeita os demais tipos.
COPY pyproject.toml uv.lock ./
RUN if [ "$WITH_DEV" = "1" ]; then \
        uv sync --locked --no-install-project; \
    else \
        uv sync --locked --no-install-project --no-dev; \
    fi


FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Bibliotecas de sistema do WeasyPrint, que gera o PDF da exportação do relatório (ADR backend 0027).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .

USER app
EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]


FROM builder AS builder-prod
# Mesmo estágio de dependências (pyproject.toml e uv.lock já estão em /app), mas sem o grupo "dev".
RUN uv sync --locked --no-install-project --no-dev


FROM python:${PYTHON_VERSION}-slim-trixie AS production

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000

# Bibliotecas de sistema do WeasyPrint, que gera o PDF da exportação do relatório (ADR backend 0027).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder-prod /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Valores fictícios só para este comando (não ficam na imagem): o collectstatic carrega os settings,
# que exigem chave e banco, mas não se conecta ao banco.
RUN DJANGO_SECRET_KEY=build-only DATABASE_URL=postgres://build:build@localhost/build \
    python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# O host "localhost" precisa constar em DJANGO_ALLOWED_HOSTS. Plataformas com healthcheck próprio
# (por exemplo o Railway) ignoram esta instrução.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/api/health' % os.environ['PORT'], timeout=3)"]

CMD ["gunicorn", "-c", "gunicorn.conf.py", "config.wsgi:application"]
