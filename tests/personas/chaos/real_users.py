"""The random real user of the chaos test: real accounts that act through the service layer."""

import datetime
import random
from collections.abc import Callable

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.deactivation import deactivate_user
from apps.accounts.models import Course, Student, Teacher, User
from apps.activities.models import Activity, ActivityStatus, ReviewDecision
from apps.activities.services import (
    add_comment,
    change_activity_status,
    create_activity,
    delete_activity,
    deliver_submission,
    edit_activity,
    review_activity,
)
from apps.advising.models import Advising, AdvisingRequest, AdvisingStatus, AnswerAction
from apps.advising.services import answer_request, end_advising, request_advising
from apps.advising.thesis import write_topic
from apps.meetings.models import Meeting, MeetingStatus
from apps.meetings.services import (
    answer_meeting,
    cancel_meeting,
    create_meeting,
    edit_meeting,
    mark_meeting_held,
    write_meeting_notes,
)
from apps.personas.simulated_teacher import SIMULATED_TEACHER_EMAIL
from tests.factories import make_student, make_teacher

Attempt = Callable[[], object]

MAX_REAL_USERS_OF_EACH_KIND = 5
END_REASON = "A orientação foi encerrada pelo professor real durante o teste."
MEETING_HOURS = range(8, 17)


def _pdf() -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "entrega.pdf", b"%PDF-1.4\n% real\n%%EOF\n", content_type="application/pdf"
    )


class RandomRealUser:
    """Real students and teachers that do whatever the random generator says, in any order.

    Every `plan_*` method reads the database, picks a target and returns the call to attempt, or
    None when there is nothing to act on. The call goes through the service layer exactly as the
    API would call it, so the services answer with their own domain errors when the action does
    not make sense any more. Only active accounts act, as a deactivated account has no session.
    """

    WEIGHTS = {
        "request_advising": 8,
        "answer_request": 6,
        "create_activity": 6,
        "edit_activity": 4,
        "delete_activity": 2,
        "review_activity": 8,
        "end_advising": 2,
        "change_status": 8,
        "deliver": 8,
        "comment": 3,
        "write_topic": 2,
        "propose_meeting": 5,
        "answer_meeting": 4,
        "edit_meeting": 3,
        "cancel_meeting": 3,
        "mark_held": 2,
        "write_notes": 1,
        "deactivate": 1,
        "toggle_availability": 1,
        "join": 2,
        "facilitator_assigns_activity": 4,
    }

    def __init__(self, rng: random.Random, course: Course, areas: list) -> None:
        self.rng = rng
        self.course = course
        self.areas = areas
        self.teacher_user_ids: set[int] = set()
        self.student_user_ids: set[int] = set()

    @property
    def user_ids(self) -> set[int]:
        return self.teacher_user_ids | self.student_user_ids

    def join_as_student(self) -> Student:
        student = make_student(course=self.course, areas=self.areas)
        self.student_user_ids.add(student.user_id)
        return student

    def join_as_teacher(self) -> Teacher:
        teacher = make_teacher(courses=[self.course], areas=self.areas)
        self.teacher_user_ids.add(teacher.user_id)
        return teacher

    def plan(self, name: str) -> Attempt | None:
        return getattr(self, f"plan_{name}")()

    def _pick(self, queryset: QuerySet):
        items = list(queryset.order_by("pk"))
        return self.rng.choice(items) if items else None

    def _prefer(self, preferred: QuerySet, fallback: QuerySet) -> QuerySet:
        """Mostly the targets that make the action meaningful, sometimes anything at all."""
        return preferred if self.rng.random() < 0.75 and preferred.exists() else fallback

    def _students(self) -> QuerySet[Student]:
        return Student.objects.select_related("user").filter(
            user_id__in=self.student_user_ids, user__is_active=True
        )

    def _teachers(self) -> QuerySet[Teacher]:
        return Teacher.objects.select_related("user").filter(
            user_id__in=self.teacher_user_ids, user__is_active=True
        )

    def _advisings_of_students(self) -> QuerySet[Advising]:
        return Advising.objects.select_related("student__user", "teacher__user").filter(
            student__user_id__in=self.student_user_ids, student__user__is_active=True
        )

    def _advisings_of_teachers(self) -> QuerySet[Advising]:
        return Advising.objects.select_related("student__user", "teacher__user").filter(
            teacher__user_id__in=self.teacher_user_ids, teacher__user__is_active=True
        )

    def _activities_of_teachers(self) -> QuerySet[Activity]:
        return Activity.objects.select_related("advising__teacher__user").filter(
            advising__teacher__user_id__in=self.teacher_user_ids,
            advising__teacher__user__is_active=True,
        )

    def _activities_of_students(self) -> QuerySet[Activity]:
        return Activity.objects.select_related("advising__student__user").filter(
            advising__student__user_id__in=self.student_user_ids,
            advising__student__user__is_active=True,
        )

    def _meetings_with_a_real_party(self) -> QuerySet[Meeting]:
        return Meeting.objects.select_related(
            "advising__student__user", "advising__teacher__user"
        ).filter(
            Q(advising__student__user_id__in=self.student_user_ids)
            | Q(advising__teacher__user_id__in=self.teacher_user_ids)
        )

    def _a_real_party_of(self, advising: Advising) -> User | None:
        parties = [
            user
            for user in (advising.student.user, advising.teacher.user)
            if user.pk in self.user_ids and user.is_active
        ]
        return self.rng.choice(parties) if parties else None

    def _day(self) -> datetime.date:
        return timezone.localdate() + datetime.timedelta(days=self.rng.randint(1, 6))

    def _meeting_times(self) -> tuple[datetime.time, datetime.time]:
        hour = self.rng.choice(MEETING_HOURS)
        return datetime.time(hour), datetime.time(hour + 1)

    def plan_request_advising(self) -> Attempt | None:
        student = self._pick(self._students())
        simulated = Teacher.objects.filter(user__email__iexact=SIMULATED_TEACHER_EMAIL).first()
        candidates = [teacher.user_id for teacher in self._teachers()]
        if simulated is not None:
            candidates += [simulated.user_id] * max(1, len(candidates))
        if student is None or not candidates:
            return None
        teacher_id = self.rng.choice(candidates)
        return lambda: request_advising(student, teacher_id)

    def plan_answer_request(self) -> Attempt | None:
        request = self._pick(
            AdvisingRequest.objects.select_related("teacher__user").filter(
                teacher__user_id__in=self.teacher_user_ids, teacher__user__is_active=True
            )
        )
        if request is None:
            return None
        action = self.rng.choice([AnswerAction.ACCEPT, AnswerAction.ACCEPT, AnswerAction.REJECT])
        return lambda: answer_request(request.teacher, request.pk, action)

    def plan_create_activity(self) -> Attempt | None:
        advising = self._pick(self._advisings_of_teachers().filter(status=AdvisingStatus.ACTIVE))
        if advising is None:
            return None
        deadline = timezone.localdate() + datetime.timedelta(days=self.rng.randint(0, 20))
        return lambda: create_activity(
            advising.teacher, advising.student.user_id, "Atividade do caos", "Descrição", deadline
        )

    def plan_facilitator_assigns_activity(self) -> Attempt | None:
        """Nobody in the real flow creates activities for the simulated teacher's students, so
        the test does it as that teacher to give its review rule something to review."""
        simulated = (
            Teacher.objects.select_related("user")
            .filter(user__email__iexact=SIMULATED_TEACHER_EMAIL)
            .first()
        )
        advising = self._pick(
            Advising.objects.select_related("student__user").filter(
                teacher=simulated, status=AdvisingStatus.ACTIVE
            )
        )
        if simulated is None or advising is None:
            return None
        deadline = timezone.localdate() + datetime.timedelta(days=self.rng.randint(0, 20))
        return lambda: create_activity(
            simulated, advising.student.user_id, "Atividade do aluno real", "Descrição", deadline
        )

    def plan_edit_activity(self) -> Attempt | None:
        activity = self._pick(self._activities_of_teachers().filter(deleted_at__isnull=True))
        if activity is None:
            return None
        days = self.rng.randint(-2, 20)
        deadline = timezone.localdate() + datetime.timedelta(days=days)
        title = self.rng.choice([None, f"Título {self.rng.randint(1, 99)}"])
        return lambda: edit_activity(
            activity.advising.teacher, activity.pk, title=title, deadline=deadline
        )

    def plan_delete_activity(self) -> Attempt | None:
        activity = self._pick(self._activities_of_teachers())
        if activity is None:
            return None
        return lambda: delete_activity(activity.advising.teacher, activity.pk)

    def plan_review_activity(self) -> Attempt | None:
        activities = self._activities_of_teachers()
        activity = self._pick(
            self._prefer(activities.filter(status=ActivityStatus.IN_REVIEW), activities)
        )
        if activity is None:
            return None
        decision = self.rng.choice(list(ReviewDecision))
        justification = "Ajuste a entrega." if decision == ReviewDecision.REJECTED else ""
        if self.rng.random() < 0.1:
            justification = "" if decision == ReviewDecision.REJECTED else "Não deveria existir."
        return lambda: review_activity(
            activity.advising.teacher, activity.pk, decision, justification
        )

    def plan_end_advising(self) -> Attempt | None:
        advising = self._pick(self._advisings_of_teachers())
        if advising is None:
            return None
        return lambda: end_advising(advising.teacher, advising.pk, END_REASON)

    def plan_change_status(self) -> Attempt | None:
        activities = self._activities_of_students()
        open_to_the_student = activities.filter(
            status__in=(ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS)
        )
        activity = self._pick(self._prefer(open_to_the_student, activities))
        if activity is None:
            return None
        status = self.rng.choice(
            [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW]
        )
        return lambda: change_activity_status(activity.advising.student, activity.pk, status)

    def plan_deliver(self) -> Attempt | None:
        activity = self._pick(self._activities_of_students())
        if activity is None:
            return None
        return lambda: deliver_submission(activity.advising.student, activity.pk, [_pdf()])

    def plan_comment(self) -> Attempt | None:
        activity = self._pick(
            Activity.objects.select_related("advising__student__user", "advising__teacher__user")
            .filter(
                Q(advising__student__user_id__in=self.student_user_ids)
                | Q(advising__teacher__user_id__in=self.teacher_user_ids)
            )
            .filter(deleted_at__isnull=True)
        )
        user = self._a_real_party_of(activity.advising) if activity else None
        if user is None:
            return None
        return lambda: add_comment(user, activity.pk, "Comentário do usuário real.")

    def plan_write_topic(self) -> Attempt | None:
        advising = self._pick(
            Advising.objects.filter(
                Q(student__user_id__in=self.student_user_ids)
                | Q(teacher__user_id__in=self.teacher_user_ids)
            )
        )
        if advising is None:
            return None
        description = f"Tema {self.rng.randint(1, 99)}"
        return lambda: write_topic(advising, description)

    def plan_propose_meeting(self) -> Attempt | None:
        advising = self._pick(
            Advising.objects.select_related("student__user", "teacher__user").filter(
                Q(student__user_id__in=self.student_user_ids)
                | Q(teacher__user_id__in=self.teacher_user_ids),
                status=AdvisingStatus.ACTIVE,
            )
        )
        author = self._a_real_party_of(advising) if advising else None
        if author is None:
            return None
        day, (at, end_at) = self._day(), self._meeting_times()
        student_id = None if author.pk == advising.student.user_id else advising.student.user_id
        return lambda: create_meeting(author, day, at, end_at, "Reunião do caos", student_id)

    def _pick_meeting_and_actor(self, meetings: QuerySet[Meeting]) -> tuple[Meeting, User] | None:
        meeting = self._pick(meetings)
        actor = self._a_real_party_of(meeting.advising) if meeting else None
        return None if actor is None else (meeting, actor)

    def plan_answer_meeting(self) -> Attempt | None:
        meetings = self._meetings_with_a_real_party()
        target = self._pick_meeting_and_actor(
            self._prefer(meetings.filter(status=MeetingStatus.PENDING), meetings)
        )
        if target is None:
            return None
        meeting, actor = target
        if self.rng.random() < 0.7:
            return lambda: answer_meeting(actor, meeting.pk, AnswerAction.ACCEPT, None)
        reason = self.rng.choice(["Sem horário disponível.", None])
        return lambda: answer_meeting(actor, meeting.pk, AnswerAction.REJECT, reason)

    def plan_edit_meeting(self) -> Attempt | None:
        target = self._pick_meeting_and_actor(self._meetings_with_a_real_party())
        if target is None:
            return None
        meeting, actor = target
        day = self._day() if self.rng.random() < 0.5 else None
        at, end_at = self._meeting_times() if self.rng.random() < 0.5 else (None, None)
        description = self.rng.choice([None, f"Descrição {self.rng.randint(1, 99)}"])
        return lambda: edit_meeting(actor, meeting.pk, day, at, end_at, description)

    def plan_cancel_meeting(self) -> Attempt | None:
        target = self._pick_meeting_and_actor(self._meetings_with_a_real_party())
        if target is None:
            return None
        meeting, actor = target
        return lambda: cancel_meeting(actor, meeting.pk, "Imprevisto do usuário real.")

    def plan_mark_held(self) -> Attempt | None:
        meeting = self._pick(
            self._meetings_with_a_real_party().filter(status=MeetingStatus.ACCEPTED)
        )
        if meeting is None or meeting.advising.teacher.user_id not in self.teacher_user_ids:
            return None
        return lambda: mark_meeting_held(meeting.advising.teacher.user, meeting.pk)

    def plan_write_notes(self) -> Attempt | None:
        meeting = self._pick(self._meetings_with_a_real_party())
        if meeting is None or meeting.advising.teacher.user_id not in self.teacher_user_ids:
            return None
        return lambda: write_meeting_notes(
            meeting.advising.teacher.user, meeting.pk, "Apontamento."
        )

    def plan_deactivate(self) -> Attempt | None:
        user = self._pick(User.objects.filter(pk__in=self.user_ids, is_active=True))
        if user is None:
            return None
        return lambda: deactivate_user(user)

    def plan_toggle_availability(self) -> Attempt | None:
        teacher = self._pick(self._teachers())
        if teacher is None:
            return None

        def toggle() -> None:
            teacher.is_available = not teacher.is_available
            teacher.save(update_fields=["is_available"])

        return toggle

    def plan_join(self) -> Attempt | None:
        if self.rng.random() < 0.5:
            if len(self.student_user_ids) >= MAX_REAL_USERS_OF_EACH_KIND:
                return None
            return self.join_as_student
        if len(self.teacher_user_ids) >= MAX_REAL_USERS_OF_EACH_KIND:
            return None
        return self.join_as_teacher
