#!/bin/sh
# Passo de release: aplica as migrations e carrega o catálogo UMA vez por implantação, antes de as
# réplicas novas começarem a atender. A inicialização do servidor (gunicorn) não migra.
# Na plataforma, configure este script como comando de release/pre-deploy:  sh docker/release.sh
set -eu

python manage.py migrate --noinput

# Catálogo (instituto, curso e áreas de interesse): sem ele ninguém se cadastra e as contas simuladas
# não são criadas. É idempotente; rodar de novo restaura os valores do arquivo, mas não apaga nada.
exec python manage.py loaddata fixtures/seed.json
