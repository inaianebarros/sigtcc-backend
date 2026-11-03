# sigtcc-backend

API do SIGTCC (Sistema de Gestão de Trabalhos de Conclusão de Curso da UFOPA), em Python, Django e Django Ninja Extra, com PostgreSQL.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gerencia o Python 3.13 e as dependências)
- Um PostgreSQL acessível (o ambiente completo em contêineres vem com a issue #15)

## Desenvolvimento local

```bash
cp .env.example .env        # ajuste DATABASE_URL e DJANGO_SECRET_KEY
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

- API: http://127.0.0.1:8000/api/ — documentação OpenAPI em `/api/docs`, verificação de saúde em `/api/health`.

## Qualidade

```bash
uv run pytest                 # testes (exigem PostgreSQL; criam um banco de teste)
uv run ruff check .           # lint
uv run ruff format --check .  # formatação
uv run python manage.py check
```

## Estrutura

Apps por domínio em `apps/`: `accounts`, `advising`, `activities`, `meetings` e `notifications`. As decisões de arquitetura ficam nas ADRs do repositório de documentação do projeto.
