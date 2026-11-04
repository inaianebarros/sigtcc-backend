#!/bin/sh
# Inicialização do backend em desenvolvimento: aplica migrations, carrega o seed (se existir)
# e inicia o servidor com recarga automática. Pensado para UMA instância; em produção as
# migrations devem rodar em um job de release, não em cada réplica.
set -eu

echo "[dev-start] aplicando migrations"
python manage.py migrate --noinput

SEED_FIXTURE="${SEED_FIXTURE:-fixtures/seed.json}"
if [ -f "$SEED_FIXTURE" ]; then
    echo "[dev-start] carregando seed: $SEED_FIXTURE"
    python manage.py loaddata "$SEED_FIXTURE"
else
    echo "[dev-start] seed não encontrado ($SEED_FIXTURE); ignorando"
fi

exec python manage.py runserver 0.0.0.0:8000
