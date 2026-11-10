# sigtcc-backend

API do SIGTCC (Sistema de Gestão de Trabalhos de Conclusão de Curso da UFOPA), em Python, Django e Django Ninja Extra, com PostgreSQL.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gerencia o Python 3.13 e as dependências)
- Docker com Compose v2 (ambiente completo em contêineres) ou um PostgreSQL acessível via `DATABASE_URL` (o usuário precisa poder criar a extensão `unaccent`, usada na busca sem acentos)

## Ambiente completo com Docker (recomendado)

Sobe PostgreSQL 17, backend, frontend (Vite), RustFS (S3) e Mailpit. Exige o clone do `sigtcc-frontend` ao lado deste repositório (`../sigtcc-frontend`, ou outro caminho em `FRONTEND_CONTEXT`).

```bash
cp .env.example .env     # preencha os campos vazios (gere com: openssl rand -hex 24)
docker compose up --build
```

| Serviço | Endereço (somente 127.0.0.1) |
|---|---|
| Frontend (Vite, hot reload) | http://localhost:3000 (o proxy `/api` aponta para o backend, sem CORS) |
| Backend (Django, hot reload) | http://localhost:8000/api/ e `/api/health` |
| PostgreSQL | 127.0.0.1:5432 (usuário/banco em `POSTGRES_*`) |
| RustFS (API S3 / console) | http://localhost:9000 / http://localhost:9001 (credenciais `RUSTFS_*` do `.env`) |
| Mailpit (caixa de entrada) | http://localhost:8025 (SMTP interno `mailpit:1025`) |

- Na subida o backend aplica as migrations e carrega `fixtures/seed.json` se o arquivo existir (ainda não existe; caminho configurável por `SEED_FIXTURE`).
- Teste de fumaça do RustFS (boto3) e do Mailpit: `docker compose run --rm backend python scripts/smoke_services.py`.
  - Resultado verificado em 03/11/2026: o `boto3` funciona com o RustFS 1.0.4 (criar bucket, enviar, baixar e excluir objeto), com `s3v4`, endereçamento por caminho, região `us-east-1` e os checksums padrão do boto3.
- Testes no host contra o PostgreSQL do Compose: ajuste `DATABASE_URL` no `.env` (host `127.0.0.1`, mesma senha) e rode `uv run pytest`. Dentro do contêiner: `docker compose run --rm backend pytest`.
- Parar: `docker compose down` (mantém os dados). `docker compose down -v` APAGA os volumes (banco e objetos do RustFS).
- Dependências do frontend mudaram (`package-lock.json`)? `docker compose build frontend`, depois recrie o volume de `node_modules`: `docker compose rm -sf frontend && docker volume rm sigtcc_frontend_node_modules && docker compose up -d frontend`.
- Linux: o contêiner do backend roda como UID 10001 e o do frontend como UID 1000; ajuste permissões da pasta montada se a recarga ou a escrita falhar.
- O serviço de cron da expiração de solicitações (#60) tem lugar reservado (comentado) no `compose.yaml`. Produção e CI/CD ficam na issue #64.

## Desenvolvimento local sem Docker

```bash
cp .env.example .env        # ajuste DATABASE_URL e DJANGO_SECRET_KEY
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

- API: http://127.0.0.1:8000/api/ — documentação OpenAPI em `/api/docs`, verificação de saúde em `/api/health`.

## Administrador (Django Admin)

O Administrador usa o Django Admin em `/admin/` (sessão do Django, separada do JWT), e não a API nem o frontend (ADR 0010). Ele só é criado por `createsuperuser` (sempre com o tipo `admin`); Professor e Aluno nunca entram no `/admin/`. Lá ele cadastra, edita e exclui Cursos e Áreas de Interesse; um curso (RN17) ou uma área (RN18) com Professores ou Alunos vinculados não pode ser excluído.

```bash
uv run python manage.py createsuperuser    # no Compose: docker compose run --rm backend python manage.py createsuperuser
```

## Qualidade

```bash
uv run pytest                 # testes (exigem PostgreSQL; criam um banco de teste)
uv run pytest --cov           # cobertura de linhas e ramos de accounts, advising e meetings; falha abaixo de 80% no total
./scripts/check_coverage.sh   # o mesmo, e exige 80% em cada um dos três apps (é o que a CI, #64, vai rodar)
uv run ruff check .           # lint
uv run ruff format --check .  # formatação
uv run python manage.py check
```

## Estrutura

Apps por domínio em `apps/`: `accounts`, `advising`, `activities`, `meetings` e `notifications`. As decisões de arquitetura ficam nas ADRs do repositório de documentação do projeto.

## Autenticação

Sessão por JWT (ADR backend 0001 e 0006): `POST /api/auth/login` devolve o access token (5 minutos) no corpo e grava o refresh token em cookie `httpOnly` restrito a `/api/auth`. `POST /api/auth/refresh` troca o cookie por um novo par (cada refresh token vale uma vez; reapresentar um já usado revoga todas as sessões do usuário), `POST /api/auth/logout` bloqueia o refresh token e `GET /api/auth/me` devolve o usuário do access token (`Authorization: Bearer ...`). Login, refresh e logout exigem o cabeçalho `Origin` e só aceitam as origens de `JWT_TRUSTED_ORIGINS`: quem usa curl ou Postman precisa enviá-lo (por exemplo `-H "Origin: http://localhost:3000"`). Permissões por tipo de conta: `IsStudent`, `IsTeacher` e `IsAdministrator` em `apps/accounts/permissions.py`.

### Recuperação de senha

`POST /api/auth/forgot-password` (corpo `{ "email" }`) responde sempre da mesma forma e, se existir uma conta ativa, envia o link `FRONTEND_URL/redefinir-senha?uid=...&token=...` (válido por 1 hora e utilizável uma única vez). `POST /api/auth/reset-password` (corpo `{ "uid", "token", "password", "password_confirmation" }`) define a nova senha, encerra todas as sessões do usuário (RN27) e devolve 400 para link inválido, expirado ou já usado. No Compose de desenvolvimento os e-mails aparecem na caixa do Mailpit, em http://localhost:8025.

## Execução local

O sistema é executado **somente em ambiente local** (Docker Compose, HTTP em `localhost`), sem previsão de implantação em nuvem (ADR backend 0005). As configurações padrão assumem isso: o cookie de refresh não é `Secure` no Compose, a origem confiável é `http://localhost:3000`, e as credenciais do `.env.example` são de desenvolvimento. Nada disso deve ir para um ambiente exposto; o checklist do que mudar antes de qualquer implantação está na ADR 0005. Mesmo localmente, use um `DJANGO_SECRET_KEY` com 32 bytes ou mais (por exemplo `openssl rand -hex 32`), pois ele assina os tokens.
