import datetime
from itertools import count

from django.contrib.auth import get_user_model
from django.utils import timezone

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
from apps.meetings.models import Meeting, MeetingStatus

DEFAULT_PASSWORD = "senha-longa-123"
_sequence = count(1)
User = get_user_model()


def make_user(user_type=UserType.STUDENT, email=None, full_name=None, is_active=True):
    number = next(_sequence)
    return User.objects.create_user(
        email=email or f"user{number}@example.com",
        password=DEFAULT_PASSWORD,
        full_name=full_name or f"Usuario {number}",
        type=user_type,
        is_active=is_active,
    )


def make_course(institute=None):
    institute = institute or Institute.objects.create(name=f"Instituto {next(_sequence)}")
    return Course.objects.create(institute=institute, name=f"Curso {next(_sequence)}")


def make_area():
    return AreaOfInterest.objects.create(name=f"Area {next(_sequence)}")


def make_student(registration_number=None, course=None, areas=(), full_name=None):
    student = Student.objects.create(
        user=make_user(UserType.STUDENT, full_name=full_name),
        course=course or make_course(),
        registration_number=registration_number or f"A{next(_sequence):06d}",
    )
    student.areas_of_interest.set(areas)
    return student


def make_teacher(
    registration_number=None,
    courses=(),
    areas=(),
    full_name=None,
    is_available=True,
    is_active=True,
):
    teacher = Teacher.objects.create(
        user=make_user(UserType.TEACHER, full_name=full_name, is_active=is_active),
        registration_number=registration_number or f"P{next(_sequence):06d}",
        is_available=is_available,
    )
    teacher.courses.set(courses)
    teacher.areas_of_interest.set(areas)
    return teacher


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


def make_meeting(advising=None, status=MeetingStatus.PENDING, scheduled_at=None, author=None):
    advising = advising or make_advising()
    return Meeting.objects.create(
        advising=advising,
        author=author or advising.teacher.user,
        scheduled_at=scheduled_at or timezone.now() + datetime.timedelta(days=3),
        description="Reunião de orientação",
        status=status,
        rejection_reason="Sem horário." if status == MeetingStatus.REJECTED else "",
    )
