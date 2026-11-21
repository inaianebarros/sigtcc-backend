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
- Dois serviços de cron rodam em laço com a mesma imagem do backend: `expiration-cron` (expiração das solicitações, #60) e `email-cron` (e-mail das notificações, #156). Produção e CI/CD ficam na issue #64.

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

### Teste de robustez das contas simuladas (caos)

O teste de caos (ADR 0022, issue #287) é lento e fica **fora** do conjunto padrão (`addopts` do `pyproject.toml` exclui o marcador `chaos`). Ele precisa só do PostgreSQL, sem Redis nem rede externa. Rode com:

```bash
uv run pytest -m chaos                                  # no Compose: docker compose run --rm backend pytest -m chaos
CHAOS_SEED=7 CHAOS_RUNS=1 uv run pytest -m chaos -s     # reproduz a execução da semente 7 e mostra o resumo
```

Em cada execução, dois professores reais e dois alunos reais (com orientação com o Professor simulado e com o par de Alunos simulados, o vinculado e o solicitante) agem pelos serviços de forma aleatória: solicitam, respondem, editam, repetem, cancelam, entregam, revisam, encerram orientação e desativam a conta (RN19), enquanto o `run_personas --once` roda em ciclos intercalados, às vezes no meio do que o simulado já decidiu fazer. Depois de cada passo o teste confere no banco as regras (RN04 a RN07, RN12, RN15, RN19, RN21, RN34 e RN40) e que nenhum simulado encerra orientação, desativa conta ou age fora do domínio `demo.ufopa.local`, que o vinculado nunca recusa reunião, que o solicitante nunca aceita nem entrega e só recusa com uma das justificativas fixas, e que nenhum simulado pede de novo depois de uma orientação encerrada. Um ciclo com `errors` maior que zero reprova (erro de domínio conta como `skipped`). Há também o teste da trava com dois processos (um segura a trava e o outro não age).

| Variável | Padrão | Efeito |
|---|---|---|
| `CHAOS_SEED` | `1` | primeira semente; cada execução usa uma semente seguinte |
| `CHAOS_RUNS` | `5` | número de execuções (sementes `CHAOS_SEED` até `CHAOS_SEED + CHAOS_RUNS - 1`) |
| `CHAOS_STEPS` | `150` | passos de cada execução |

A semente aparece no nome de cada teste (`seed=3`) e, numa falha, na mensagem junto dos últimos passos e do comando que reproduz. Para explorar mais, use outras sementes, por exemplo `CHAOS_SEED=1000 CHAOS_RUNS=30`.

## Estrutura

Apps por domínio em `apps/`: `accounts`, `advising`, `activities`, `meetings` e `notifications`. As decisões de arquitetura ficam nas ADRs do repositório de documentação do projeto.

## Expiração das solicitações de orientação

Uma solicitação pendente há mais de 7 dias é marcada como "Não respondida" e o aluno recebe o aviso (RN20, RF44, RF45). O comando é idempotente (só toca no que já venceu) e pode ser executado à mão:

```bash
uv run python manage.py expire_advising_requests    # no Compose: docker compose run --rm backend python manage.py expire_advising_requests
```

No Compose, o serviço `expiration-cron` (mesma imagem do backend, sem portas) o executa a cada hora, num laço. Cada solicitação é marcada e notificada na própria transação: uma falha desfaz só aquela solicitação, o comando segue com as outras e termina com erro para aparecer no log, e a hora seguinte tenta de novo. A marcação também acontece quando o aluno envia uma nova solicitação com a anterior vencida, pela mesma função, sem duplicar o aviso.

## Exportação do relatório em PDF

O aluno e o professor de uma orientação encerrada pedem o relatório dela em PDF (RF62, RN50, ADR 0027). O pedido só grava uma linha na fila; o comando `send_report_exports` gera o PDF com o WeasyPrint, grava no bucket das entregas (prefixo `exportacoes/`) e marca a exportação como pronta, e só quem pediu baixa o arquivo. Uma falha marca aquela exportação como falha e o comando segue com as outras.

```bash
uv run python manage.py send_report_exports    # no Compose: docker compose run --rm backend python manage.py send_report_exports
```

No Compose, o serviço `export-cron` (mesma imagem do backend, sem portas) o executa a cada 3 segundos, num laço, e a cada cerca de 1 hora roda `remove_expired_report_exports`, que apaga do bucket os PDFs com mais de 7 dias e os marca como expirados (RN50; o Railway Buckets não tem regra de ciclo de vida). Pedir de novo devolve a exportação válida (em andamento, ou pronta e não expirada), e o download depois de 7 dias responde 410. **Sem esse serviço nenhum PDF é gerado.** O WeasyPrint precisa de bibliotecas de sistema (pango e fontes), já instaladas nas imagens `runtime` e `production`; para rodar no host, instale `libpango-1.0-0`, `libpangoft2-1.0-0` e `libharfbuzz-subset0`.

## E-mail das notificações

Cada notificação criada também é enviada por e-mail ao destinatário (RF54, ADR 0011), em até 1 minuto (RNF04). O assunto é o tipo da notificação, e o corpo traz o mesmo texto que o sistema mostra e um link para ele (`FRONTEND_URL`). O comando pode ser executado à mão:

```bash
uv run python manage.py send_notification_emails    # no Compose: docker compose run --rm backend python manage.py send_notification_emails
```

No Compose, o serviço `email-cron` (mesma imagem do backend, sem portas) o executa a cada 30 segundos, num laço, e os e-mails aparecem no Mailpit, em http://localhost:8025. **Sem esse serviço nenhum e-mail de notificação é enviado.** A notificação nasce junto da ação (uma linha no banco), e o comando envia as pendentes em ordem de criação, uma por transação:

- uma falha do SMTP desfaz só aquela notificação, que continua pendente e ganha uma tentativa; no quinto fracasso o comando desiste dela e registra em log, e ela segue visível no sistema. O comando segue com as outras e termina com erro, para aparecer no log;
- conta desativada (RN19) não recebe e-mail, e a notificação é marcada como processada sem contar como falha;
- cada notificação é travada enquanto é enviada (`skip_locked`), então o laço e uma execução à mão ao mesmo tempo não duplicam o e-mail;
- as notificações que já existiam quando o recurso foi criado foram marcadas como enviadas pela migration, para ninguém receber o histórico;
- a seed de demonstração também gera e-mails, como qualquer ação. O Mailpit local guarda um número limitado de mensagens, então as mais antigas saem quando a seed em volume as empurra.

### E-mail de redefinição de senha

O pedido de link de redefinição de senha (RF07, ADR 0020) não envia o e-mail na requisição: grava um pedido numa fila e responde, igual para qualquer e-mail, para o tempo da resposta não revelar quais contas existem. O comando `send_password_reset_emails` (à mão ou, no Compose, no mesmo laço de 30 segundos do `email-cron`) cria o link no momento do envio, com validade de 1 hora a partir do e-mail, e o manda. **Sem o `email-cron`, o e-mail de redefinição não sai** (antes saía na hora). O e-mail pode levar até cerca de 30 segundos. Os pedidos sem conta ativa são descartados sem e-mail. Uma falha do SMTP deixa o pedido pendente e conta uma tentativa; no quinto fracasso o comando desiste dele e registra em log, e segue com os outros. Um pedido que não saiu em 15 minutos é descartado, uma conta desativada depois do pedido não recebe e-mail (e isso não conta como falha), e os pedidos já resolvidos há mais de 1 dia são apagados. Cada pedido é travado enquanto é enviado, então dois laços ao mesmo tempo não duplicam o e-mail. O pedido também é limitado por e-mail: 3 por hora, por e-mail normalizado (sem diferenciar maiúsculas nem espaços), sem olhar o IP e contando qualquer texto, exista a conta ou não (`AUTH_FORGOT_ACCOUNT_LIMIT` e `AUTH_FORGOT_ACCOUNT_WINDOW_SECONDS`). Acima do limite a resposta é o mesmo 200 de sempre, sem 429, e nada é gravado nem enviado. Se o cache falhar, o pedido segue.

```bash
uv run python manage.py send_password_reset_emails
```

Em `EMAIL_HOST` e nas demais variáveis `EMAIL_*` do `.env` fica o SMTP; `EMAIL_TIMEOUT` (padrão 10 s) evita que um servidor sem resposta trave o envio.

### E-mail pela API do Brevo (homologação)

O plano Trial do Railway bloqueia o SMTP de saída, então na homologação o e-mail das notificações e da redefinição de senha sai pela API HTTPS de e-mail transacional do Brevo (`config/brevo_email.py`, só biblioteca padrão). Os comandos e a fila (ADR 0011) não mudam: a escolha é só por configuração. Sem `EMAIL_PROVIDER`, o padrão é `smtp`, o Mailpit do desenvolvimento local.

| Variável | Uso |
|---|---|
| `EMAIL_PROVIDER` | `smtp` (padrão) ou `brevo` |
| `BREVO_API_KEY` | chave da API do Brevo (segredo, sem padrão; obrigatória com `brevo`) |
| `DEFAULT_FROM_EMAIL` | endereço do remetente, que deve estar verificado no Brevo (obrigatório com `brevo`) |
| `EMAIL_FROM_NAME` | nome exibido do remetente (padrão `SIGTCC`) |
| `BREVO_API_TIMEOUT_SECONDS` | prazo de cada chamada (padrão 10) |
| `BREVO_API_URL` | opcional, só para testes; precisa ser `https://` |

Com `EMAIL_PROVIDER=brevo`, a inicialização falha com `ImproperlyConfigured` se faltar a chave ou o remetente. Passo a passo: crie a conta no Brevo, verifique o remetente (ou o domínio) em Senders, crie uma chave de API e defina as variáveis no ambiente da homologação, nunca no repositório. Uma falha da API (indisponibilidade, prazo esgotado, cota diária excedida, remetente não verificado ou chave inválida) conta como tentativa e deixa o item pendente, como uma falha de SMTP, e o log traz só o status HTTP e o código do erro, nunca a chave.

Um remetente sem domínio próprio verificado pode cair na caixa de spam.

**Limite diário do plano gratuito:** cerca de 300 e-mails por dia (valor a confirmar na página do fornecedor). Ao ultrapassá-lo, a API responde 429 e o envio falha: a notificação interna continua existindo e visível no sistema, e a fila conta a tentativa (ADR 0011: até 5 tentativas e depois desiste). Portanto, acima do limite alguns e-mails podem não ser enviados no dia. Essa falha só vai para o log (aviso `email_daily_quota_exceeded`, sem a chave nem o endereço do destinatário) e nunca chega ao usuário: o envio acontece nos comandos agendados, fora da requisição, então nenhuma ação, nem o pedido de redefinição de senha, muda de resposta ou de tempo por causa do e-mail.

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
| Professor 1 | `prof1.orientandos` | vários orientandos |
| Professor 2 | `prof2.sem-orientandos` | sem orientandos |
| Professor 3 | `prof3.indisponivel` | indisponível |
| Administrador | `admin` | Django Admin |

O comando cria os perfis e os cenários de solicitação de orientação: o aluno 1 sem solicitação, o 2 com uma pendente (perto do prazo de 7 dias), o 3 com uma recusada e o 4 com uma não respondida (mais de 7 dias, marcada pela expiração), todas ao professor 1 e feitas pelos serviços reais, com as notificações. A lista do professor 1 tem uma só solicitação pendente: o elenco não tem outro aluno livre para ter uma segunda. As datas são relativas ao dia da execução. Os alunos 5 e 6 têm orientação ativa com o professor 1 (por solicitação e aceite). O aluno 5 tem 6 atividades: pendente, em andamento (com comentários dos dois lados), em revisão (com 2 arquivos), concluída (aceita), atrasada e uma recusada na revisão (com justificativa e um anexo do professor, de volta a pendente), com entregas em arquivo; a recusada tem a história longa da linha do tempo da atividade (RN49), com 12 eventos de todos os tipos (criação, duas versões de entrega, mudanças de situação, envio para revisão, comentários dos dois lados e a avaliação), mais de uma página de 10, e a em revisão tem uma entrega aguardando avaliação; o aluno 6 não tem tema nem atividades. As entregas usam o armazenamento de arquivos: se ele estiver fora do ar, o comando pula só as atividades que precisam de arquivo, avisa, e uma nova execução as completa. Uma atividade arquivada só existe em orientação encerrada, então fica para o histórico (#141). O aluno 7 tem uma orientação **encerrada** com o professor 1 (com tema, motivo, duas atividades que o encerramento arquivou, RN07, e duas reuniões futuras que ele cancelou pelo sistema, RN34), já livre para solicitar de novo: ela aparece no histórico dos dois. O professor 2 mostra os estados vazios. Com a opção `--volume`, o comando acrescenta 45 professores com nomes acentuados na área do aluno 1 (a busca de orientadores passa de uma página, com compatibilidades diferentes, e a busca por nome ignora acentos) e 45 orientações encerradas do professor 1, com alunos próprios (o histórico passa de uma página). Os dados de volume são identificáveis (e-mails `volume.*@demo.ufopa.local`), são inseridos em lote e rodar de novo não os duplica. O aluno 5 também tem o tema do TCC e 8 reuniões, uma em cada situação: pendente de cada autor (uma para o professor responder e outra para o aluno), aceita futura, recusada (com justificativa), cancelada pelo professor e cancelada pelo aluno (com motivo), realizada (aceita, no passado) e vencida (pendente, no passado); a cancelada **pelo sistema** só existe quando uma orientação termina, então está no aluno 7. As notificações dos dois lados vêm dos serviços, com as mais antigas já lidas e as das reuniões e dos comentários ainda por ler. Para recomeçar com datas frescas (as datas são relativas ao dia da execução e envelhecem), use o reinício:

```bash
uv run python manage.py seed_demo_data --reset          # mostra quantos usuários vai remover e pede confirmação
uv run python manage.py seed_demo_data --reset --yes    # sem a pergunta (no Compose: docker compose run --rm backend python manage.py seed_demo_data --reset --yes)
uv run python manage.py seed_demo_data --reset --yes --volume   # recria também os dados de volume
```

O `--reset` apaga **só** os usuários `@demo.ufopa.local` (inclusive os de volume) e o que é deles (solicitações, orientações, atividades, comentários, mudanças de situação, revisões e os anexos delas, entregas e os arquivos delas, reuniões, tema e notificações), numa transação: uma falha no meio desfaz tudo. Não toca em outros usuários nem no catálogo, e **recusa** apagar se um usuário real estiver ligado a um de demonstração (por exemplo, uma solicitação dele a um professor de demonstração). Sem `--volume`, os dados de volume são removidos e não voltam. Depois de apagar, o comando recria o conjunto como na execução normal.

## Autenticação

Sessão por JWT (ADR backend 0001 e 0006): `POST /api/auth/login` devolve o access token (5 minutos) no corpo e grava o refresh token em cookie `httpOnly` restrito a `/api/auth`. `POST /api/auth/refresh` troca o cookie por um novo par (cada refresh token vale uma vez; reapresentar um já usado revoga todas as sessões do usuário), `POST /api/auth/logout` bloqueia o refresh token e `GET /api/auth/me` devolve o usuário do access token (`Authorization: Bearer ...`). Login, refresh e logout exigem o cabeçalho `Origin` e só aceitam as origens de `JWT_TRUSTED_ORIGINS`: quem usa curl ou Postman precisa enviá-lo (por exemplo `-H "Origin: http://localhost:3000"`). Permissões por tipo de conta: `IsStudent`, `IsTeacher` e `IsAdministrator` em `apps/accounts/permissions.py`. As senhas aceitas têm no máximo 128 caracteres (422 no campo acima disso, sem rodar o hash), e cada rota de autenticação, inclusive `/auth/change-password`, tem também um limite de requisições por IP (variáveis `AUTH_<ESCOPO>_IP_LIMIT` e `AUTH_<ESCOPO>_IP_WINDOW_SECONDS` no `.env.example`).

### Recuperação de senha

`POST /api/auth/forgot-password` (corpo `{ "email" }`) responde sempre da mesma forma e, se existir uma conta ativa, envia o link `FRONTEND_URL/redefinir-senha?uid=...&token=...` (válido por 1 hora e utilizável uma única vez). `POST /api/auth/reset-password` (corpo `{ "uid", "token", "password", "password_confirmation" }`) define a nova senha, encerra todas as sessões do usuário (RN27) e devolve 400 para link inválido, expirado ou já usado. No Compose de desenvolvimento os e-mails aparecem na caixa do Mailpit, em http://localhost:8025.

## Execução local

O sistema é executado **somente em ambiente local** (Docker Compose, HTTP em `localhost`), sem previsão de implantação em nuvem (ADR backend 0005). As configurações padrão assumem isso: o cookie de refresh não é `Secure` no Compose, a origem confiável é `http://localhost:3000`, e as credenciais do `.env.example` são de desenvolvimento. Nada disso deve ir para um ambiente exposto; o checklist do que mudar antes de qualquer implantação está na ADR 0005. Mesmo localmente, use um `DJANGO_SECRET_KEY` com 32 bytes ou mais (por exemplo `openssl rand -hex 32`), pois ele assina os tokens.

## Servidor de produção (homologação)

O alvo `production` do `Dockerfile` (`docker build --target production -t sigtcc-backend:prod .`, também o padrão sem `--target`) é a imagem para um ambiente exposto. O alvo `runtime` continua sendo o do Compose de desenvolvimento (com `runserver`).

- **Inicialização:** `gunicorn -c gunicorn.conf.py config.wsgi:application`, com **1 worker e 4 threads** (ADR backend 0021). Escuta em `0.0.0.0:$PORT` (padrão 8000), registra acesso e erro na saída padrão e roda como usuário sem privilégios. Não aplica migrations.
- **Uma única réplica, sempre:** os contadores de limitação de tentativas (ADR 0007) ficam no cache local do processo (`locmemcache`, sem Redis). Com mais de um worker ou réplica cada processo teria o seu contador e o limite deixaria de valer. Para escalar, é preciso antes um cache compartilhado (`CACHE_URL`).
- **Estáticos:** `collectstatic` roda no build da imagem e o WhiteNoise, ativo só com `DJANGO_DEBUG=false`, serve os arquivos (Django Admin) pelo próprio processo.
- **Passo de release:** `sh docker/release.sh` roda `migrate --noinput` e carrega o catálogo (`loaddata fixtures/seed.json`: instituto, curso e áreas de interesse, sem o qual ninguém se cadastra e as contas simuladas não são criadas; é idempotente, e rodar de novo restaura os valores do arquivo). A plataforma deve chamá-lo uma vez por implantação, antes de iniciar o servidor (no Railway, como comando de pre-deploy; configurado na issue #288). Aplicar migrations é idempotente, mas não rode o release em paralelo.
- **Variáveis obrigatórias:** `DJANGO_SECRET_KEY` (32 bytes ou mais), `DJANGO_DEBUG=false`, `DJANGO_ALLOWED_HOSTS` e `DATABASE_URL`, além das de e-mail, S3, JWT e `NUM_PROXIES` descritas no `.env.example`. Nada disso entra na imagem.
- **Trabalhos agendados em um só serviço:** `sh docker/workers.sh` roda, num laço único com tique de 15 s, `run_personas` (a cada tique), `send_notification_emails` e `send_password_reset_emails` (a cada 2 tiques, 30 s) e `expire_advising_requests` (a cada 240 tiques, 1 h). No Compose cada trabalho tem o seu serviço; no Railway o plano limita o número de serviços, então um só serviço usa este script como comando de início (ele precisa de `PERSONAS_ENABLED=true` e das mesmas variáveis do backend). Uma falha de um comando não derruba o laço. `WORKERS_TICK_SECONDS` serve para testar com tiques curtos.
- **Armazenamento no Railway Buckets:** as credenciais do bucket (`railway bucket credentials`) dão o endereço (`AWS_S3_ENDPOINT_URL`), a região (`AWS_S3_REGION_NAME`, `auto`), o nome real do bucket (`S3_BUCKET`, com sufixo) e as chaves (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`). Defina `AWS_S3_ADDRESSING_STYLE=virtual`: o Railway só aceita endereço no estilo virtual-hosted, e o padrão `path` (RustFS local) não funciona nele.
- **Healthcheck:** a imagem traz um `HEALTHCHECK` que consulta `http://localhost:$PORT/api/health`, então `localhost` precisa estar em `DJANGO_ALLOWED_HOSTS`. Plataformas com sonda própria (como o Railway) ignoram esse `HEALTHCHECK`; a sonda delas precisa de um Host permitido.
