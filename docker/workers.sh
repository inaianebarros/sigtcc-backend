#!/bin/sh
# Laço único dos trabalhos agendados da homologação, para um só serviço do Railway (o plano limita o
# número de serviços). Cada comando segue o seu intervalo original, a partir de um tique de 15 s:
#   run_personas                                         a cada tique         (15 s, ADR 0022)
#   send_notification_emails, send_password_reset_emails a cada 2 tiques      (30 s, ADR 0011)
#   expire_advising_requests                             a cada 240 tiques    (1 h, ADR 0003)
# Uma falha de um comando nunca derruba o laço nem os outros comandos. WORKERS_TICK_SECONDS existe
# para testar o script com tiques curtos.
set -u

tick_seconds="${WORKERS_TICK_SECONDS:-15}"
tick=0

while true; do
    python manage.py run_personas --once || true
    if [ $((tick % 2)) -eq 0 ]; then
        python manage.py send_notification_emails || true
        python manage.py send_password_reset_emails || true
    fi
    if [ $((tick % 240)) -eq 0 ]; then
        python manage.py expire_advising_requests || true
    fi
    tick=$((tick + 1))
    sleep "$tick_seconds"
done
