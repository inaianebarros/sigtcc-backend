from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class FieldChange:
    """A field of an edit: what it was and what it became, for whoever tells the other party."""

    old: str | date | datetime
    new: str | date | datetime
