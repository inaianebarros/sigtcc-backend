# syntax=docker/dockerfile:1

# Imagem do backend (Django + Ninja Extra). O comando padrão roda o servidor de
# desenvolvimento; o servidor de produção e a imagem de release ficam para a issue #64.
ARG PYTHON_VERSION=3.13.18

FROM python:${PYTHON_VERSION}-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# WITH_DEV=1 inclui o grupo "dev" (pytest e ruff); use 0 para uma imagem só com runtime.
ARG WITH_DEV=1
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    if [ "$WITH_DEV" = "1" ]; then \
        uv sync --locked --no-install-project; \
    else \
        uv sync --locked --no-install-project --no-dev; \
    fi


FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app . .

USER app
EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
