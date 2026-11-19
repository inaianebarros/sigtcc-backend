#!/bin/sh
# Passo de release: aplica as migrations UMA vez por implantação, antes de as réplicas novas
# começarem a atender. A inicialização do servidor (gunicorn) não migra.
# Na plataforma, configure este script como comando de release/pre-deploy:  sh docker/release.sh
set -eu

exec python manage.py migrate --noinput
