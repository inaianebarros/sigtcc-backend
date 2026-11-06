import logging
import math
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from .client_ip import client_ip
from .errors import TooManyRequestsError

logger = logging.getLogger("sigtcc.auth")


def _reset_key(key: str) -> str:
    return f"{key}:reset_at"


def _seconds_left(key: str, window: int) -> int:
    reset_at = cache.get(_reset_key(key))
    if reset_at is None:
        return window
    return max(1, math.ceil(reset_at - time.time()))


def hit(key: str, window: int) -> tuple[int, int]:
    """Conta uma ocorrência na janela fixa e devolve (total, segundos até a janela acabar)."""
    if cache.add(key, 1, window):
        cache.set(_reset_key(key), time.time() + window, window)
        return 1, window
    try:
        count = cache.incr(key)
    except ValueError:  # a janela terminou entre add e incr
        cache.set(key, 1, window)
        cache.set(_reset_key(key), time.time() + window, window)
        return 1, window
    return count, _seconds_left(key, window)


def peek(key: str, window: int) -> tuple[int, int]:
    count = cache.get(key, 0)
    return count, _seconds_left(key, window) if count else window


def clear(key: str) -> None:
    cache.delete_many([key, _reset_key(key)])


def enforce_ip_limit(scope: str, request: HttpRequest) -> None:
    """Conta a requisição do IP no escopo e responde 429 quando passa do limite."""
    limit, window = settings.AUTH_RATE_LIMITS[scope]
    ip = client_ip(request)
    count, retry_after = hit(f"rl:{scope}:{ip}", window)
    if count > limit:
        logger.warning(
            "rate limited",
            extra={
                "event": "rate_limited",
                "scope": scope,
                "client_ip": ip,
                "retry_after": retry_after,
            },
        )
        raise TooManyRequestsError(retry_after)
