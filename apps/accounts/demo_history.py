"""The ended advising of student 7, made with the real services: the history of the demonstration.

Student 7 asks teacher 1, is accepted, gets a thesis topic and two open activities, and the teacher
ends the advising, which archives the activities (RN07) and tells the student. The dates are then
moved back so the advising reads as old. It runs only for a student with no advising yet.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.activities.models import ActivityStatus
from apps.activities.services import change_activity_status, create_activity
from apps.advising.models import Advising, AdvisingRequest, AnswerAction
from apps.advising.services import answer_request, end_advising, request_advising
from apps.advising.thesis import write_topic

from .demo_scenarios import RESPONSE_DELAY, demo_advisor, demo_student
from .models import Student, Teacher

ENDED_STUDENT = "aluno7.orientacao-encerrada"

STARTED_AGE = timedelta(days=90)
ENDED_AGE = timedelta(days=20)
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


def _move_the_dates_back(advising: Advising, advising_request: AdvisingRequest) -> None:
    now = timezone.now()
    started = now - STARTED_AGE
    Advising.objects.filter(pk=advising.pk).update(started_at=started, ended_at=now - ENDED_AGE)
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
    end_advising(teacher, advising.pk, END_REASON)
    _move_the_dates_back(advising, advising_request)
    return 1
