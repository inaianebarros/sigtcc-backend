import datetime as dt
from datetime import datetime
from typing import Annotated, Literal

from ninja import Field, Schema
from pydantic import StringConstraints

from apps.accounts.models import Student, Teacher
from apps.accounts.schemas import AreaOfInterestOut

from .deadlines import answer_deadline_of
from .models import ANSWERED_STATUSES, Advising, AdvisingRequest, AnswerAction, BlockedReason
from .thesis import topic_of

END_REASON_MIN_LENGTH = 10
END_REASON_MAX_LENGTH = 500

EndReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=END_REASON_MIN_LENGTH, max_length=END_REASON_MAX_LENGTH
    ),
]


class EndAdvisingIn(Schema):
    reason: EndReason


class AdvisingRequestIn(Schema):
    teacher_id: int


class TeacherSummaryOut(Schema):
    id: int
    full_name: str

    @classmethod
    def from_teacher(cls, teacher: Teacher) -> "TeacherSummaryOut":
        return cls(id=teacher.user_id, full_name=teacher.user.full_name)


class AdvisingRequestOut(Schema):
    id: int
    status: str
    requested_at: datetime
    teacher: TeacherSummaryOut

    @classmethod
    def from_request(cls, advising_request: AdvisingRequest) -> "AdvisingRequestOut":
        teacher = advising_request.teacher
        return cls(
            id=advising_request.id,
            status=advising_request.status,
            requested_at=advising_request.requested_at,
            teacher=TeacherSummaryOut.from_teacher(teacher),
        )


class CourseSummaryOut(Schema):
    id: int
    name: str


class ProfileAreaOut(AreaOfInterestOut):
    is_common: bool

    @classmethod
    def mark_common(cls, areas, common_ids: set[int]) -> "list[ProfileAreaOut]":
        return [cls(id=area.id, name=area.name, is_common=area.id in common_ids) for area in areas]


class StudentSummaryOut(Schema):
    id: int
    full_name: str
    course: CourseSummaryOut
    areas: list[AreaOfInterestOut]


class AdvisingRequestTimingOut(Schema):
    id: int
    status: str
    requested_at: datetime
    responded_at: datetime | None
    expires_at: datetime | None

    @staticmethod
    def resolve_status(obj: AdvisingRequest) -> str:
        return obj.effective_status

    @staticmethod
    def resolve_expires_at(obj: AdvisingRequest) -> datetime | None:
        if obj.effective_status in ANSWERED_STATUSES:
            return None
        return answer_deadline_of(obj.requested_at)


class AdvisingRequestItemOut(AdvisingRequestTimingOut):
    student: StudentSummaryOut

    @staticmethod
    def resolve_student(obj: AdvisingRequest) -> StudentSummaryOut:
        student = obj.student
        areas = sorted(student.areas_of_interest.all(), key=lambda area: area.name)
        return StudentSummaryOut(
            id=student.user_id,
            full_name=student.user.full_name,
            course=CourseSummaryOut(id=student.course_id, name=student.course.name),
            areas=[AreaOfInterestOut(id=area.id, name=area.name) for area in areas],
        )


class AdvisingRequestAnswerIn(Schema):
    action: AnswerAction


class AdvisingOut(Schema):
    id: int
    started_at: datetime


class EndedAdvisingOut(Schema):
    id: int
    status: str
    started_at: datetime
    ended_at: datetime
    reason: str = Field(alias="end_reason")


class PartySummaryOut(Schema):
    id: int
    full_name: str


class AdvisingHistorySummaryOut(Schema):
    """The numbers on top of the history of advisings (RF49)."""

    ended_count: int
    #: The teacher's active advisings; null for a student, who has at most one.
    active_count: int | None


class EndedAdvisingItemOut(Schema):
    """One ended advising in the history of a student or of a teacher (RF49)."""

    id: int
    counterpart: PartySummaryOut
    started_at: datetime
    ended_at: datetime
    reason: str = Field(alias="end_reason")
    thesis_topic: str | None

    @staticmethod
    def resolve_counterpart(obj: Advising) -> PartySummaryOut:
        return PartySummaryOut(id=obj.counterpart_id, full_name=obj.counterpart_name)

    @staticmethod
    def resolve_thesis_topic(obj: Advising) -> str | None:
        topic = topic_of(obj)
        return topic.description if topic else None


class AdvisingReportOut(EndedAdvisingItemOut):
    """The header and the general totals of the report of an ended advising (RF61)."""

    records_count: int
    meetings_count: int
    activities_count: int


class MeetingRecordOut(Schema):
    """A meeting that took place, as a record of the report (RF61): its day and its content."""

    type: Literal["meeting"]
    id: int
    #: The day the meeting started, in the institution's time zone.
    date: dt.date
    starts_at: datetime
    ends_at: datetime
    description: str
    #: The teacher's notes (RF58); empty text when there are none.
    notes: str


class ActivityRecordOut(Schema):
    """An activity that was concluded or archived, as a record of the report (RF61)."""

    type: Literal["activity"]
    id: int
    #: The deadline, the day the record is ordered and shown by.
    date: dt.date
    deadline: dt.date
    title: str
    description: str
    status: Literal["completed", "archived"]


RecordOut = Annotated[MeetingRecordOut | ActivityRecordOut, Field(discriminator="type")]


class AdvisingRequestAnswerOut(AdvisingRequestItemOut):
    advising: AdvisingOut | None

    @staticmethod
    def resolve_advising(obj: AdvisingRequest) -> AdvisingOut | None:
        advising = Advising.objects.filter(request=obj).first()
        if advising is None:
            return None
        return AdvisingOut(id=advising.id, started_at=advising.started_at)


class AdvisingWithTeacherOut(AdvisingOut):
    teacher: TeacherSummaryOut

    @staticmethod
    def resolve_teacher(obj: Advising) -> TeacherSummaryOut:
        return TeacherSummaryOut.from_teacher(obj.teacher)


class StudentRequestOut(AdvisingRequestTimingOut):
    teacher: TeacherSummaryOut

    @staticmethod
    def resolve_teacher(obj: AdvisingRequest) -> TeacherSummaryOut:
        return TeacherSummaryOut.from_teacher(obj.teacher)


class RequestStatusOut(Schema):
    request: StudentRequestOut | None
    advising: AdvisingWithTeacherOut | None
    can_request: bool
    blocked_reason: BlockedReason | None


class AdviseeStudentOut(Schema):
    id: int
    full_name: str
    course: CourseSummaryOut

    @staticmethod
    def resolve_id(obj: Student) -> int:
        return obj.user_id

    @staticmethod
    def resolve_full_name(obj: Student) -> str:
        return obj.user.full_name

    @staticmethod
    def resolve_course(obj: Student) -> CourseSummaryOut:
        return CourseSummaryOut(id=obj.course_id, name=obj.course.name)


class ActivityCountsOut(Schema):
    pending: int
    in_progress: int
    in_review: int
    completed: int


class CalendarAdviseeOut(Schema):
    id: int
    full_name: str
    ended: bool

    @staticmethod
    def resolve_id(obj: Student) -> int:
        return obj.user_id

    @staticmethod
    def resolve_full_name(obj: Student) -> str:
        return obj.user.full_name

    @staticmethod
    def resolve_ended(obj: Student) -> bool:
        return not obj.has_active


class AdviseeOut(Schema):
    advising_id: int
    student: AdviseeStudentOut
    started_at: datetime
    thesis_topic: str | None
    activities: ActivityCountsOut

    @staticmethod
    def resolve_advising_id(obj: Advising) -> int:
        return obj.id

    @staticmethod
    def resolve_thesis_topic(obj: Advising) -> str | None:
        topic = topic_of(obj)
        return topic.description if topic else None

    @staticmethod
    def resolve_activities(obj: Advising) -> ActivityCountsOut:
        return ActivityCountsOut(
            pending=obj.pending_count,
            in_progress=obj.in_progress_count,
            in_review=obj.in_review_count,
            completed=obj.completed_count,
        )
