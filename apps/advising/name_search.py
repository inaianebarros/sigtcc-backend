from django.db.models import QuerySet


def filter_by_name(queryset: QuerySet, lookup: str, text: str | None) -> QuerySet:
    """Keeps the rows whose name contains the text, ignoring case and accents.

    The ends of the text are trimmed and an empty text is no filter. A null byte, which the
    database refuses, matches nothing.
    """
    text = (text or "").strip()
    if "\x00" in text:
        return queryset.none()
    if text:
        return queryset.filter(**{f"{lookup}__unaccent__icontains": text})
    return queryset
