import hashlib
import logging
import unicodedata

from django.conf import settings
from django.http import HttpRequest

from config import rate_limit
from config.client_ip import client_ip
from config.errors import TooManyRequestsError

logger = logging.getLogger("sigtcc.auth")


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", email).strip().casefold()


def _key(scope: str, subject: str, ip: str) -> str:
    digest = hashlib.sha256(f"{subject}|{ip}".encode()).hexdigest()
    return f"rl:{scope}:{digest}"


def ensure_not_blocked(scope: str, subject: str, request: HttpRequest) -> None:
    """Refuse the attempt, even with the right secret, while the subject is at the limit."""
    limit, window = settings.AUTH_RATE_LIMITS[scope]
    ip = client_ip(request)
    try:
        failures, retry_after = rate_limit.peek(_key(scope, subject, ip), window)
    except Exception:
        rate_limit.fail_open(scope)
        return
    if failures >= limit:
        logger.warning(
            "attempt blocked",
            extra={
                "event": "attempt_blocked",
                "scope": scope,
                "client_ip": ip,
                "failures": failures,
                "retry_after": retry_after,
            },
        )
        raise TooManyRequestsError(retry_after)


def record_failure(scope: str, subject: str, request: HttpRequest, user_id: int | None) -> None:
    _, window = settings.AUTH_RATE_LIMITS[scope]
    ip = client_ip(request)
    try:
        failures, _ = rate_limit.hit(_key(scope, subject, ip), window)
    except Exception:
        rate_limit.fail_open(scope)
        return
    logger.warning(
        "attempt failed",
        extra={
            "event": "attempt_failed",
            "scope": scope,
            "client_ip": ip,
            "user_id": user_id,
            "failures": failures,
        },
    )


def clear_failures(scope: str, subject: str, request: HttpRequest) -> None:
    try:
        rate_limit.clear(_key(scope, subject, client_ip(request)))
    except Exception:
        rate_limit.fail_open(scope)
