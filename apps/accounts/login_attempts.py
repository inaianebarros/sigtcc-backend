import hashlib
import logging

from django.conf import settings
from django.http import HttpRequest

from config import rate_limit
from config.client_ip import client_ip
from config.errors import TooManyRequestsError

from .models import User

logger = logging.getLogger("sigtcc.auth")
SCOPE = "login_failures"


def _key(email: str, ip: str) -> str:
    digest = hashlib.sha256(f"{email.strip().lower()}|{ip}".encode()).hexdigest()
    return f"rl:{SCOPE}:{digest}"


def ensure_not_blocked(email: str, request: HttpRequest) -> None:
    """Bloqueia a tentativa, mesmo com a senha certa, enquanto a conta estiver no limite."""
    limit, window = settings.AUTH_RATE_LIMITS[SCOPE]
    ip = client_ip(request)
    failures, retry_after = rate_limit.peek(_key(email, ip), window)
    if failures >= limit:
        logger.warning(
            "login blocked",
            extra={
                "event": "login_blocked",
                "scope": SCOPE,
                "client_ip": ip,
                "failures": failures,
                "retry_after": retry_after,
            },
        )
        raise TooManyRequestsError(retry_after)


def record_failure(email: str, request: HttpRequest) -> None:
    _, window = settings.AUTH_RATE_LIMITS[SCOPE]
    ip = client_ip(request)
    failures, _ = rate_limit.hit(_key(email, ip), window)
    user_id = User.objects.filter(email__iexact=email).values_list("id", flat=True).first()
    logger.warning(
        "login failed",
        extra={
            "event": "login_failed",
            "scope": SCOPE,
            "client_ip": ip,
            "user_id": user_id,
            "failures": failures,
        },
    )


def clear_failures(email: str, request: HttpRequest) -> None:
    rate_limit.clear(_key(email, client_ip(request)))
