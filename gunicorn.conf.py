"""Configuração do gunicorn de produção (ADR backend 0021).

Um único worker com várias threads: o cache dos limites de autenticação (ADR 0007) é local ao
processo, então mais de um worker, ou mais de uma réplica, dividiria os contadores.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 1
threads = 4
accesslog = "-"
errorlog = "-"
# Sem usuário com home gravável na imagem; o socket de controle (gunicornc) não é usado.
control_socket_disable = True
