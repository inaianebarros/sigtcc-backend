#!/usr/bin/env sh
# Cobertura de linhas e de ramos (#55): roda os testes e exige no mínimo 80% em CADA app, não só
# no total. A configuração (ramos, pastas medidas e o limite do total) fica em pyproject.toml.
# Uma falta de cobertura vira teste novo, nunca exclusão da medida.
set -eu

MINIMUM=80

uv run pytest --cov -q

status=0
for app in accounts advising meetings; do
    if uv run coverage report --include="apps/${app}/*" --fail-under="${MINIMUM}" >/dev/null; then
        echo "apps/${app}: cobertura de pelo menos ${MINIMUM}%"
    else
        echo "apps/${app}: cobertura ABAIXO de ${MINIMUM}%" >&2
        status=1
    fi
done
exit "${status}"
