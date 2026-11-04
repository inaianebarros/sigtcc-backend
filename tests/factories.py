import datetime
from itertools import count

from django.contrib.auth import get_user_model

from apps.accounts.models import (
    AreaOfInterest,
    Course,
    Institute,
    Student,
    Teacher,
    UserType,
)
from apps.activities.models import Activity
from apps.advising.models import Advising, AdvisingRequest

DEFAULT_PASSWORD = "senha-longa-123"
_sequence = count(1)
User = get_user_model()


def make_user(user_type=UserType.STUDENT, email=None):
    number = next(_sequence)
    return User.objects.create_user(
        email=email or f"user{number}@example.com",
        password=DEFAULT_PASSWORD,
        full_name=f"Usuario {number}",
        type=user_type,
    )


def make_course():
    institute = Institute.objects.create(name=f"Instituto {next(_sequence)}")
    return Course.objects.create(institute=institute, name=f"Curso {next(_sequence)}")


def make_area():
    return AreaOfInterest.objects.create(name=f"Area {next(_sequence)}")


def make_student(registration_number=None, course=None):
    return Student.objects.create(
        user=make_user(UserType.STUDENT),
        course=course or make_course(),
        registration_number=registration_number or f"A{next(_sequence):06d}",
    )


def make_teacher(registration_number=None):
    return Teacher.objects.create(
        user=make_user(UserType.TEACHER),
        registration_number=registration_number or f"P{next(_sequence):06d}",
    )


def make_advising_request(student=None, teacher=None):
    return AdvisingRequest.objects.create(
        student=student or make_student(), teacher=teacher or make_teacher()
    )


def make_advising(student=None, teacher=None):
    return Advising.objects.create(
        student=student or make_student(), teacher=teacher or make_teacher()
    )


def make_activity(advising=None):
    return Activity.objects.create(
        advising=advising or make_advising(),
        title="Revisão bibliográfica",
        description="Entregar o capítulo 2",
        deadline=datetime.date(2026, 11, 20),
    )
