from functools import reduce
from operator import or_

from django.db.models import Q, QuerySet


def filter_by_any_text(queryset: QuerySet, lookups: list[str], text: str | None) -> QuerySet:
    """Keeps the rows where at least one of the fields contains the text, ignoring case and accents.

    The ends of the text are trimmed and an empty text is no filter. A null byte, which the
    database refuses, matches nothing.
    """
    text = (text or "").strip()
    if "\x00" in text:
        return queryset.none()
    if text:
        match = reduce(or_, (Q(**{f"{lookup}__unaccent__icontains": text}) for lookup in lookups))
        return queryset.filter(match)
    return queryset


def filter_by_name(queryset: QuerySet, lookup: str, text: str | None) -> QuerySet:
    """Keeps the rows whose name contains the text, ignoring case and accents."""
    return filter_by_any_text(queryset, [lookup], text)
