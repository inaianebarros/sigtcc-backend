from smtplib import SMTPException

from django.core.mail.backends import locmem
from django.core.mail.backends.base import BaseEmailBackend


class FailingEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise SMTPException("mail server unavailable")


class FailingForOneAddressEmailBackend(locmem.EmailBackend):
    """Delivers to the in-memory outbox, except to the address that always fails."""

    FAILING_ADDRESS = "falha@example.com"

    def send_messages(self, email_messages):
        if any(self.FAILING_ADDRESS in message.to for message in email_messages):
            raise SMTPException("recipient rejected")
        return super().send_messages(email_messages)
