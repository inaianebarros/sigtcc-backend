from datetime import datetime

from ninja import Schema

from apps.accounts.schemas import AreaOfInterestOut

from .deadlines import answer_deadline_of
from .models import AdvisingRequest


class AdvisingRequestIn(Schema):
    teacher_id: int


class TeacherSummaryOut(Schema):
    id: int
    full_name: str


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
            teacher=TeacherSummaryOut(id=teacher.user_id, full_name=teacher.user.full_name),
        )


class CourseSummaryOut(Schema):
    id: int
    name: str


class StudentSummaryOut(Schema):
    id: int
    full_name: str
    course: CourseSummaryOut
    areas: list[AreaOfInterestOut]


class AdvisingRequestItemOut(Schema):
    id: int
    status: str
    requested_at: datetime
    responded_at: datetime | None
    expires_at: datetime
    student: StudentSummaryOut

    @staticmethod
    def resolve_status(obj: AdvisingRequest) -> str:
        return obj.effective_status

    @staticmethod
    def resolve_expires_at(obj: AdvisingRequest) -> datetime:
        return answer_deadline_of(obj.requested_at)

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
