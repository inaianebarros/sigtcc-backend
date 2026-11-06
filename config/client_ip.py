from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """IP do cliente. Só confia em X-Forwarded-For quando NUM_PROXIES indica proxies conhecidos."""
    remote_address = request.META.get("REMOTE_ADDR", "")
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if settings.NUM_PROXIES <= 0 or not forwarded_for:
        return remote_address
    addresses = [address.strip() for address in forwarded_for.split(",")]
    return addresses[-min(settings.NUM_PROXIES, len(addresses))]
