"""Django e-mail backend that sends through the Brevo transactional e-mail HTTPS API (ADR 0011,
issue #300). The Railway Trial plan blocks outgoing SMTP, so homologation uses this instead.

It only handles what the system sends: plain text messages to a list of recipients. A failure of
any kind raises `BrevoDeliveryError`, which the e-mail commands already treat as a failed attempt.
The API key is sent only in the `api-key` header and never appears in a message or in a log."""

import json
import logging
import re
from collections.abc import Sequence
from email.utils import parseaddr
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger("sigtcc.notifications")

QUOTA_EXCEEDED_STATUS = 429
ERROR_CODE_PATTERN = re.compile(r"[a-z_]{1,64}")

HTTP_ERROR_REASONS = {
    400: "request rejected (for example an unverified sender)",
    401: "API key rejected",
    403: "API key not allowed to send",
    429: "daily quota or rate limit exceeded",
}


class BrevoDeliveryError(Exception):
    """The Brevo API did not accept the message."""


class _NoRedirects(HTTPRedirectHandler):
    # A redirect would resend the `api-key` header to wherever it points.
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = build_opener(_NoRedirects)


def _error_code(error: HTTPError) -> str:
    try:
        code = json.loads(error.read(4096)).get("code")
    except (ValueError, AttributeError, OSError):
        return "unknown"
    return code if isinstance(code, str) and ERROR_CODE_PATTERN.fullmatch(code) else "unknown"


def _address_entries(addresses: Sequence[str]) -> list[dict[str, str]]:
    return [{"email": parseaddr(address)[1]} for address in addresses]


class BrevoEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = settings.BREVO_API_KEY
        self.api_url = settings.BREVO_API_URL
        self.timeout = settings.BREVO_API_TIMEOUT_SECONDS
        if not self.api_key:
            raise ImproperlyConfigured("BREVO_API_KEY is required by the Brevo e-mail backend.")
        if not self.api_url.startswith("https://"):
            raise ImproperlyConfigured("BREVO_API_URL must be an https:// URL.")

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
            except BrevoDeliveryError:
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message: EmailMessage) -> None:
        if message.attachments or getattr(message, "alternatives", None):
            raise BrevoDeliveryError("attachments and alternative bodies are not supported")
        request = Request(
            self.api_url,
            data=json.dumps(self._payload(message)).encode(),
            headers={
                "api-key": self.api_key,
                "accept": "application/json",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with _OPENER.open(request, timeout=self.timeout):
                return
        except HTTPError as error:
            reason = HTTP_ERROR_REASONS.get(error.code, "unexpected response")
            if error.code == QUOTA_EXCEEDED_STATUS:
                logger.warning("email_daily_quota_exceeded: Brevo API answered HTTP 429")
            raise BrevoDeliveryError(
                f"Brevo API returned HTTP {error.code} ({_error_code(error)}): {reason}"
            ) from None
        except (OSError, HTTPException) as error:
            # The cause is not chained: the text of a network error is not ours to vouch for.
            raise BrevoDeliveryError(f"Brevo API unreachable ({type(error).__name__})") from None

    @staticmethod
    def _payload(message: EmailMessage) -> dict:
        from_name, from_address = parseaddr(message.from_email)
        sender = {"email": from_address, "name": settings.EMAIL_FROM_NAME or from_name}
        payload = {
            "sender": {key: value for key, value in sender.items() if value},
            "to": _address_entries(message.to),
            "subject": message.subject,
            "textContent": message.body,
        }
        if message.cc:
            payload["cc"] = _address_entries(message.cc)
        if message.bcc:
            payload["bcc"] = _address_entries(message.bcc)
        if message.reply_to:
            payload["replyTo"] = _address_entries(message.reply_to[:1])[0]
        return payload
