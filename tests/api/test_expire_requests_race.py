"""The command racing with the marking of the same request notifies only once (#60, ADR 0004)."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.advising.expiration import expire_request
from apps.advising.models import AdvisingRequest, AdvisingRequestStatus
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import send_while_a_lock_is_held
from tests.factories import make_advising_request

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_request_marked_while_the_command_waits_for_its_lock_is_not_notified_twice():
    advising_request = make_advising_request()
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=timezone.now() - timedelta(days=9)
    )

    def mark_while_holding_the_lock(wait_for_the_request):
        AdvisingRequest.objects.select_for_update().get(pk=advising_request.pk)
        wait_for_the_request()
        assert expire_request(advising_request.pk) is True

    send_while_a_lock_is_held(
        mark_while_holding_the_lock,
        lambda: call_command("expire_advising_requests", stdout=StringIO()),
    )

    advising_request.refresh_from_db()
    assert advising_request.status == AdvisingRequestStatus.UNANSWERED
    notifications = Notification.objects.filter(type=NotificationType.ADVISING_REQUEST_UNANSWERED)
    assert notifications.count() == 1
