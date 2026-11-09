from datetime import date, datetime

from ninja import Schema

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


class StudentDashboardOut(Schema):
    has_active_advising: bool
    advising: DashboardAdvisingOut | None
    activities: ActivityIndicatorsOut | None
    next_activities: NextActivitiesOut | None
    last_request: LastRequestOut | None
    has_ended_advisings: bool
