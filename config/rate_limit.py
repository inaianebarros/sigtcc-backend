import logging
import math
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from .client_ip import client_ip
from .errors import TooManyRequestsError

logger = logging.getLogger("sigtcc.auth")

_ATTEMPTS = 3


def _reset_key(key: str) -> str:
    return f"{key}:reset_at"


def _seconds_left(key: str, window: int) -> int:
    reset_at = cache.get(_reset_key(key))
    if reset_at is None:
        return window
    return max(1, math.ceil(reset_at - time.time()))


def hit(key: str, window: int) -> tuple[int, int]:
    """Count one hit in a fixed window; return the total and the seconds until the window ends.

    The counter and its reset time live in separate keys, so under heavy concurrency the wait
    reported can be overestimated (never longer than the window) and a hit can be miscounted.
    """
    for _ in range(_ATTEMPTS):
        if cache.add(key, 1, window):
            cache.set(_reset_key(key), time.time() + window, window)
            return 1, window
        try:
            return cache.incr(key), _seconds_left(key, window)
        except ValueError:  # the window ended between add and incr: start a new one
            continue
    return 1, window


def peek(key: str, window: int) -> tuple[int, int]:
    count = cache.get(key, 0)
    return count, _seconds_left(key, window) if count else window


def clear(key: str) -> None:
    cache.delete_many([key, _reset_key(key)])


def fail_open(action: str):
    """Log a cache failure and let the request through, so a broken cache never blocks logins."""
    logger.exception(
        "rate limit cache failure", extra={"event": "rate_limit_cache_error", "scope": action}
    )


def enforce_ip_limit(scope: str, request: HttpRequest) -> None:
    """Count the request against its client address and answer 429 once the scope's limit is passed."""
    limit, window = settings.AUTH_RATE_LIMITS[scope]
    ip = client_ip(request)
    try:
        count, retry_after = hit(f"rl:{scope}:{ip}", window)
    except Exception:
        fail_open(scope)
        return
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
