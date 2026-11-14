"""The ended advising of student 7, made with the real services: the history of the demonstration.

Student 7 asks teacher 1, is accepted, gets a thesis topic, a concluded activity, two open
activities, a held meeting with notes and two meetings to come, and the teacher ends the advising,
which archives the open activities and cancels the future meetings (RN07, RN34) and tells the
student. The concluded activity and the held meeting stay, so the report of the advising (RF61) has
records of both types. The dates are then moved back so the advising reads as old. It runs only
for a student with no advising yet.
"""

from datetime import time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.activities.models import Activity, ActivityStatus
from apps.activities.services import change_activity_status, create_activity
from apps.advising.models import Advising, AdvisingRequest, AnswerAction
from apps.advising.services import answer_request, end_advising, request_advising
from apps.advising.thesis import write_topic
from apps.meetings.models import Meeting
from apps.meetings.services import (
    answer_meeting,
    create_meeting,
    mark_meeting_held,
    write_meeting_notes,
)

from .demo_scenarios import RESPONSE_DELAY, demo_advisor, demo_student
from .models import Student, Teacher

ENDED_STUDENT = "aluno7.orientacao-encerrada"

STARTED_AGE = timedelta(days=90)
ENDED_AGE = timedelta(days=20)
ACTIVITY_DELAY = timedelta(days=10)
END_REASON = (
    "Concluímos o ciclo de orientação deste TCC e o aluno vai defender com outro professor."
)
THESIS_TOPIC = "Sistemas distribuídos para o processamento de dados educacionais em nuvem"
CONCLUDED_ACTIVITY = (
    "Revisão da literatura",
    "Levantar e resumir os trabalhos relacionados ao tema.",
)
HELD_MEETING = "Acompanhar o andamento da prova de conceito"
HELD_MEETING_NOTES = "Aluno apresentou o protótipo. Ajustar a coleta de dados e rever as métricas."
OPEN_ACTIVITIES = (
    ("Estudo de arquiteturas distribuídas", "Comparar arquiteturas de processamento distribuído."),
    ("Implantação em nuvem", "Publicar a prova de conceito em um provedor de nuvem."),
)


def _open_the_activities(teacher: Teacher, student: Student) -> None:
    deadline = timezone.localdate() + timedelta(days=15)
    activities = [
        create_activity(teacher, student.user_id, title, description, deadline)
        for title, description in OPEN_ACTIVITIES
    ]
    change_activity_status(student, activities[0].pk, ActivityStatus.IN_PROGRESS)


def _conclude_an_activity(teacher: Teacher, student: Student) -> None:
    """An activity concluded before the ending: it stays in the report (RF61). The delivery that
    would lead to it needs a stored file, so the status is set directly."""
    title, description = CONCLUDED_ACTIVITY
    deadline = timezone.localdate() + timedelta(days=15)
    activity = create_activity(teacher, student.user_id, title, description, deadline)
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.COMPLETED)


def _hold_a_meeting(teacher: Teacher, student: Student) -> None:
    """A meeting the student accepted and the teacher marked as held, with notes (RN43, RN44): the
    ending keeps it, so it is a record of the report (RF61)."""
    day = timezone.localdate() + timedelta(days=3)
    meeting = create_meeting(
        teacher.user, day, time(9, 0), time(10, 0), HELD_MEETING, student.user_id
    )
    answer_meeting(student.user, meeting.pk, AnswerAction.ACCEPT, None)
    mark_meeting_held(teacher.user, meeting.pk)
    write_meeting_notes(teacher.user, meeting.pk, HELD_MEETING_NOTES)


def _schedule_the_meetings(teacher: Teacher, student: Student) -> None:
    """A pending and an accepted meeting, both still to come: the ending cancels them (RN34)."""
    day = timezone.localdate() + timedelta(days=5)
    create_meeting(
        teacher.user, day, time(10, 0), time(11, 0), "Alinhar o escopo do trabalho", student.user_id
    )
    accepted = create_meeting(
        teacher.user,
        day + timedelta(days=2),
        time(14, 0),
        time(15, 0),
        "Revisar a prova de conceito",
        student.user_id,
    )
    answer_meeting(student.user, accepted.pk, AnswerAction.ACCEPT, None)


def _move_the_dates_back(advising: Advising, advising_request: AdvisingRequest) -> None:
    """Ages the whole story: it started long ago and ended `ENDED_AGE` ago, with its activities."""
    now = timezone.now()
    started = now - STARTED_AGE
    ended = now - ENDED_AGE
    Advising.objects.filter(pk=advising.pk).update(started_at=started, ended_at=ended)
    Activity.objects.filter(advising=advising).update(
        created_at=started + ACTIVITY_DELAY, deadline=(ended - timedelta(days=3)).date()
    )
    held_start = ended - timedelta(days=5)
    Meeting.objects.filter(advising=advising, held_at__isnull=False).update(
        scheduled_at=held_start,
        ends_at=held_start + timedelta(hours=1),
        held_at=held_start - timedelta(hours=1),
    )
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=started - RESPONSE_DELAY, responded_at=started
    )


@transaction.atomic
def seed_ended_advising() -> int:
    """Creates the ended advising of student 7 if it has none; returns how many it made."""
    student = demo_student(ENDED_STUDENT)
    if Advising.objects.filter(student=student).exists():
        return 0
    teacher = demo_advisor()
    advising_request = request_advising(student, teacher.user_id)
    answer_request(teacher, advising_request.pk, AnswerAction.ACCEPT)
    advising = Advising.objects.get(request=advising_request)
    write_topic(advising, THESIS_TOPIC)
    _conclude_an_activity(teacher, student)
    _open_the_activities(teacher, student)
    _hold_a_meeting(teacher, student)
    _schedule_the_meetings(teacher, student)
    end_advising(teacher, advising.pk, END_REASON)
    _move_the_dates_back(advising, advising_request)
    return 1
