import ipaddress

from django.conf import settings
from django.http import HttpRequest

UNKNOWN_ADDRESS = "unknown"


def _valid_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def client_ip(request: HttpRequest) -> str:
    """Return the client address, reading X-Forwarded-For only when NUM_PROXIES says to trust it.

    With N trusted proxies the client is the N-th address from the end of the header, the one the
    outermost trusted proxy appended; anything a client wrote before it is ignored. A malformed
    entry falls back to the connection address.
    """
    remote_address = request.META.get("REMOTE_ADDR") or UNKNOWN_ADDRESS
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if settings.NUM_PROXIES <= 0 or not forwarded_for:
        return remote_address
    addresses = [address.strip() for address in forwarded_for.split(",")]
    candidate = addresses[-min(settings.NUM_PROXIES, len(addresses))]
    return candidate if _valid_address(candidate) else remote_address
