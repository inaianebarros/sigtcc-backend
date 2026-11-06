from datetime import datetime

from ninja import Schema

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
