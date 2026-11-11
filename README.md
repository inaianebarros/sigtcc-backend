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

O Administrador usa o Django Admin em `/admin/` (sessão do Django, separada do JWT), e não a API nem o frontend (ADR 0010). Ele só é criado por `createsuperuser` (sempre com o tipo `admin`); Professor e Aluno nunca entram no `/admin/`. Lá ele cadastra, edita e exclui Cursos e Áreas de Interesse; um curso (RN17) ou uma área (RN18) com Professores ou Alunos vinculados não pode ser excluído. Os Usuários aparecem em lista (com filtros por perfil, instituto e curso) e só podem ser desativados, pela ação "Excluir (desativar) usuário", que encerra os vínculos ativos (RN19); a página é somente leitura, sem cadastro nem exclusão física.

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

## Expiração das solicitações de orientação

Uma solicitação pendente há mais de 7 dias é marcada como "Não respondida" e o aluno recebe o aviso (RN20, RF44, RF45). O comando é idempotente (só toca no que já venceu) e pode ser executado à mão:

```bash
uv run python manage.py expire_advising_requests    # no Compose: docker compose run --rm backend python manage.py expire_advising_requests
```

No Compose, o serviço `expiration-cron` (mesma imagem do backend, sem portas) o executa a cada hora, num laço. Cada solicitação é marcada e notificada na própria transação: uma falha desfaz só aquela solicitação, o comando segue com as outras e termina com erro para aparecer no log, e a hora seguinte tenta de novo. A marcação também acontece quando o aluno envia uma nova solicitação com a anterior vencida, pela mesma função, sem duplicar o aviso.

## Dados de demonstração

Para testar todas as telas com Aluno, Professor e Administrador, o comando `seed_demo_data` cria os usuários de demonstração. **Só para uso local**: eles têm uma senha conhecida, e o comando recusa rodar com `DJANGO_DEBUG` falso (ADR 0005). Ele usa o cadastro real da aplicação, é idempotente (cria só o que falta) e precisa do catálogo carregado (`loaddata fixtures/seed.json`, que o Compose já faz na subida se o arquivo existir):

```bash
uv run python manage.py seed_demo_data    # no Compose: docker compose run --rm backend python manage.py seed_demo_data
```

Todos entram com a senha `Senha-Demo-2026`, e os e-mails terminam em `@demo.ufopa.local`:

| Usuário | E-mail (antes do `@`) | Cenário |
|---|---|---|
| Aluno 1 | `aluno1.sem-orientacao` | sem orientação nem solicitação |
| Aluno 2 | `aluno2.solicitacao-pendente` | solicitação pendente |
| Aluno 3 | `aluno3.solicitacao-recusada` | solicitação recusada |
| Aluno 4 | `aluno4.solicitacao-nao-respondida` | solicitação não respondida |
| Aluno 5 | `aluno5.orientacao-completa` | orientação ativa completa |
| Aluno 6 | `aluno6.orientacao-vazia` | orientação ativa vazia |
| Aluno 7 | `aluno7.orientacao-encerrada` | orientação encerrada (histórico) |
| Aluno 8 | `aluno8.outro-curso` | de outro curso (isolamento) |
| Professor 1 | `prof1.orientandos` | vários orientandos |
| Professor 2 | `prof2.sem-orientandos` | sem orientandos |
| Professor 3 | `prof3.indisponivel` | indisponível |
| Professor 4 | `prof4.outro-curso` | de outro curso |
| Administrador | `admin` | Django Admin |

O comando cria os perfis, o curso "Sistemas de Informação" e os cenários de solicitação de orientação: o aluno 1 sem solicitação, o 2 com uma pendente (perto do prazo de 7 dias), o 3 com uma recusada e o 4 com uma não respondida (mais de 7 dias, marcada pela expiração), todas ao professor 1 e feitas pelos serviços reais, com as notificações. A lista do professor 1 tem uma só solicitação pendente: o elenco não tem outro aluno livre para ter uma segunda. As datas são relativas ao dia da execução. Os alunos 5 e 6 têm orientação ativa com o professor 1 (por solicitação e aceite). O aluno 5 tem 6 atividades: pendente, em andamento (com comentários dos dois lados), em revisão (com 2 arquivos), concluída (aceita), atrasada e uma recusada na revisão (com justificativa, de volta a pendente), com entregas em arquivo; o aluno 6 não tem tema nem atividades. As entregas usam o armazenamento de arquivos: se ele estiver fora do ar, o comando pula só as atividades que precisam de arquivo, avisa, e uma nova execução as completa. Uma atividade arquivada só existe em orientação encerrada, então fica para o histórico (#141). Os demais cenários (reuniões, tema, histórico) chegam nas próximas issues da série (#141 a #144).

## Autenticação

Sessão por JWT (ADR backend 0001 e 0006): `POST /api/auth/login` devolve o access token (5 minutos) no corpo e grava o refresh token em cookie `httpOnly` restrito a `/api/auth`. `POST /api/auth/refresh` troca o cookie por um novo par (cada refresh token vale uma vez; reapresentar um já usado revoga todas as sessões do usuário), `POST /api/auth/logout` bloqueia o refresh token e `GET /api/auth/me` devolve o usuário do access token (`Authorization: Bearer ...`). Login, refresh e logout exigem o cabeçalho `Origin` e só aceitam as origens de `JWT_TRUSTED_ORIGINS`: quem usa curl ou Postman precisa enviá-lo (por exemplo `-H "Origin: http://localhost:3000"`). Permissões por tipo de conta: `IsStudent`, `IsTeacher` e `IsAdministrator` em `apps/accounts/permissions.py`. As senhas aceitas têm no máximo 128 caracteres (422 no campo acima disso, sem rodar o hash), e cada rota de autenticação, inclusive `/auth/change-password`, tem também um limite de requisições por IP (variáveis `AUTH_<ESCOPO>_IP_LIMIT` e `AUTH_<ESCOPO>_IP_WINDOW_SECONDS` no `.env.example`).

### Recuperação de senha

`POST /api/auth/forgot-password` (corpo `{ "email" }`) responde sempre da mesma forma e, se existir uma conta ativa, envia o link `FRONTEND_URL/redefinir-senha?uid=...&token=...` (válido por 1 hora e utilizável uma única vez). `POST /api/auth/reset-password` (corpo `{ "uid", "token", "password", "password_confirmation" }`) define a nova senha, encerra todas as sessões do usuário (RN27) e devolve 400 para link inválido, expirado ou já usado. No Compose de desenvolvimento os e-mails aparecem na caixa do Mailpit, em http://localhost:8025.

## Execução local

O sistema é executado **somente em ambiente local** (Docker Compose, HTTP em `localhost`), sem previsão de implantação em nuvem (ADR backend 0005). As configurações padrão assumem isso: o cookie de refresh não é `Secure` no Compose, a origem confiável é `http://localhost:3000`, e as credenciais do `.env.example` são de desenvolvimento. Nada disso deve ir para um ambiente exposto; o checklist do que mudar antes de qualquer implantação está na ADR 0005. Mesmo localmente, use um `DJANGO_SECRET_KEY` com 32 bytes ou mais (por exemplo `openssl rand -hex 32`), pois ele assina os tokens.
