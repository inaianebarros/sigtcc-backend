from datetime import datetime, timedelta

from django.utils import timezone

ANSWER_DEADLINE = timedelta(days=7)


def answer_cutoff() -> datetime:
    return timezone.now() - ANSWER_DEADLINE
