import datetime as dt
from typing import Annotated, Literal

from ninja import Field, Schema


class TimelineAuthorOut(Schema):
    id: int
    full_name: str
    type: Literal["student", "teacher"]


class TimelineFileOut(Schema):
    id: int
    file_name: str
    size_bytes: int
    content_type: str


class TimelineEventBase(Schema):
    """What every event of the timeline has (RN49): its index, the id of the record it comes from,
    when it happened and who did it."""

    index: int
    id: int
    occurred_at: dt.datetime
    author: TimelineAuthorOut


class CreatedEventOut(TimelineEventBase):
    type: Literal["created"]


class DeliveryEventOut(TimelineEventBase):
    type: Literal["delivery"]
    #: The position of the delivery in the activity, from 1.
    version: int
    files: list[TimelineFileOut]
    #: True on the latest delivery while the activity is in review with no decision after it.
    awaiting_review: bool


class StatusChangeEventOut(TimelineEventBase):
    type: Literal["status_change"]
    status: Literal["pending", "in_progress"]


class SentForReviewEventOut(TimelineEventBase):
    type: Literal["sent_for_review"]


class CommentEventOut(TimelineEventBase):
    type: Literal["comment"]
    text: str


class ReviewEventOut(TimelineEventBase):
    type: Literal["review"]
    decision: Literal["accepted", "rejected"]
    justification: str
    #: The version of the latest delivery before the decision; null if there was none.
    version: int | None
    files: list[TimelineFileOut]


TimelineEventOut = Annotated[
    CreatedEventOut
    | DeliveryEventOut
    | StatusChangeEventOut
    | SentForReviewEventOut
    | CommentEventOut
    | ReviewEventOut,
    Field(discriminator="type"),
]


class TimelineOut(Schema):
    items: list[TimelineEventOut]
    #: The number of events of the activity, which is also the highest index.
    count: int
    #: Whether there are events with an index lower than the last one returned.
    has_more: bool
