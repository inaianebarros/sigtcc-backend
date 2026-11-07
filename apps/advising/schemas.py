from datetime import datetime

from ninja import Schema

from apps.accounts.models import Teacher
from apps.accounts.schemas import AreaOfInterestOut

from .deadlines import answer_deadline_of
from .models import ANSWERED_STATUSES, Advising, AdvisingRequest, AnswerAction, BlockedReason


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
