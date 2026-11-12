"""The ended advising of student 7, made with the real services: the history of the demonstration.

Student 7 asks teacher 1, is accepted, gets a thesis topic, two open activities and two meetings
to come, and the teacher ends the advising, which archives the activities and cancels the
meetings (RN07, RN34) and tells the student. The dates are then
moved back so the advising reads as old. It runs only for a student with no advising yet.
"""

from datetime import time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.activities.models import Activity, ActivityStatus
from apps.activities.services import change_activity_status, create_activity
from apps.advising.models import Advising, AdvisingRequest, AnswerAction
from apps.advising.services import answer_request, end_advising, request_advising
from apps.advising.thesis import write_topic
from apps.meetings.services import answer_meeting, create_meeting

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
    _open_the_activities(teacher, student)
    _schedule_the_meetings(teacher, student)
    end_advising(teacher, advising.pk, END_REASON)
    _move_the_dates_back(advising, advising_request)
    return 1
