from datetime import date, datetime

from ninja import Schema

from apps.activities.models import Activity
from apps.advising.schemas import TeacherSummaryOut


class DashboardAdvisingOut(Schema):
    teacher: TeacherSummaryOut
    thesis_topic: str | None


class ActivityIndicatorsOut(Schema):
    total: int
    pending: int
    in_progress: int
    in_review: int
    completed: int
    overdue: int
    progress_percent: int | None


class DashboardActivityOut(Schema):
    id: int
    title: str
    deadline: date
    status: str
    overdue: bool


class NextActivitiesOut(Schema):
    items: list[DashboardActivityOut]
    has_more: bool


class LastRequestOut(Schema):
    id: int
    status: str
    requested_at: datetime
    teacher: TeacherSummaryOut


class StudentMeetingOut(Schema):
    id: int
    scheduled_at: datetime
    ends_at: datetime
    teacher: TeacherSummaryOut
    description: str


class StudentMeetingsOut(Schema):
    items: list[StudentMeetingOut]
    has_more: bool


class StudentDashboardOut(Schema):
    has_active_advising: bool
    advising: DashboardAdvisingOut | None
    activities: ActivityIndicatorsOut | None
    next_activities: NextActivitiesOut | None
    next_meetings: StudentMeetingsOut | None
    last_request: LastRequestOut | None
    has_ended_advisings: bool


class UserRefOut(Schema):
    id: int
    full_name: str


class AttentionAdviseeOut(UserRefOut):
    thesis_topic: str | None
    overdue_count: int
    in_review_count: int


class AttentionAdviseesOut(Schema):
    items: list[AttentionAdviseeOut]
    has_more: bool


class DeadlineOut(DashboardActivityOut):
    student: UserRefOut

    @staticmethod
    def resolve_student(obj: Activity) -> UserRefOut:
        student = obj.advising.student
        return UserRefOut(id=student.user_id, full_name=student.user.full_name)


class DeadlinesOut(Schema):
    items: list[DeadlineOut]
    has_more: bool


class TeacherMeetingOut(Schema):
    id: int
    scheduled_at: datetime
    ends_at: datetime
    student: UserRefOut
    description: str


class TeacherMeetingsOut(Schema):
    items: list[TeacherMeetingOut]
    has_more: bool


class TeacherDashboardOut(Schema):
    advisees_count: int
    activities: ActivityIndicatorsOut | None
    attention_advisees: AttentionAdviseesOut | None
    next_deadlines: DeadlinesOut | None
    next_meetings: TeacherMeetingsOut | None
