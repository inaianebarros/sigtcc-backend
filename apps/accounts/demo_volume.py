"""Extra demonstration data in volume, to try pagination, the page in the URL and the name search.

Teachers with accented names in the area of student 1 (so the search of student 1 passes the page
size) and ended advisings of teacher 1 with students made for it (so the history of teacher 1
does too). It is bulk data, not a scenario of the rules: it is inserted directly, with one shared
password hash, and every e-mail starts with `VOLUME_PREFIX` so it is easy to tell apart.
"""

from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from apps.advising.models import Advising, AdvisingStatus

from .demo_scenarios import demo_advisor
from .demo_seed import DEMO_DOMAIN, DEMO_PASSWORD, OWN_COURSE, DemoSeedError
from .models import AreaOfInterest, Course, Student, Teacher, User, UserType

VOLUME_PREFIX = "volume."
VOLUME_TEACHERS = 45
VOLUME_ADVISINGS = 45

TEACHER_FIRST_NAMES = (
    "João",
    "Ângela",
    "Letícia",
    "Sérgio",
    "Cláudia",
    "Vinícius",
    "Luíza",
    "Hélio",
    "Bárbara",
)
TEACHER_SURNAMES = ("Conceição", "Magalhães", "Araújo", "Guimarães", "Nascimento")
STUDENT_FIRST_NAMES = ("Thaís", "Heitor", "Valéria", "Mônica", "Rúbia")
STUDENT_SURNAMES = (
    "Cavalcante",
    "Pimentel",
    "Siqueira",
    "Barros",
    "Ferraz",
    "Moreira",
    "Dias",
    "Lopes",
    "Teixeira",
)
SHARED_AREAS = ("Inteligência Artificial", "Aprendizado de Máquina", "Ciência de Dados")
OTHER_AREAS = ("Computação Gráfica", "Banco de Dados", "Redes de Computadores")
END_REASONS = (
    "Orientação concluída com a defesa do trabalho.",
    "O aluno mudou de tema e vai procurar outro orientador.",
    "Encerramos de comum acordo ao fim do semestre letivo.",
)
ADVISING_LENGTH = timedelta(days=60)


def _email(kind: str, number: int) -> str:
    return f"{VOLUME_PREFIX}{kind}{number:02d}@{DEMO_DOMAIN}"


def _missing(kind: str, total: int) -> list[int]:
    existing = set(
        User.objects.filter(email__startswith=f"{VOLUME_PREFIX}{kind}").values_list(
            "email", flat=True
        )
    )
    return [number for number in range(1, total + 1) if _email(kind, number) not in existing]


def _areas(names: tuple[str, ...]) -> dict[str, AreaOfInterest]:
    areas = {area.name: area for area in AreaOfInterest.objects.filter(name__in=names)}
    if len(areas) != len(names):
        raise DemoSeedError("Faltam áreas de interesse no catálogo.")
    return areas


def _create_users(kind: str, numbers: list[int], full_name, user_type: UserType) -> list[User]:
    password = make_password(DEMO_PASSWORD)
    users = [
        User(email=_email(kind, n), full_name=full_name(n), type=user_type, password=password)
        for n in numbers
    ]
    return User.objects.bulk_create(users)


def _create_teachers(numbers: list[int], course: Course) -> None:
    areas = _areas(SHARED_AREAS + OTHER_AREAS)
    users = _create_users(
        "prof",
        numbers,
        lambda n: f"{TEACHER_FIRST_NAMES[(n - 1) % 9]} {TEACHER_SURNAMES[(n - 1) // 9]}",
        UserType.TEACHER,
    )
    teachers = Teacher.objects.bulk_create(
        [
            Teacher(user=user, registration_number=f"PVOL{n:03d}")
            for user, n in zip(users, numbers, strict=True)
        ]
    )
    Teacher.courses.through.objects.bulk_create(
        [Teacher.courses.through(teacher_id=t.pk, course_id=course.id) for t in teachers]
    )
    links = []
    for teacher, number in zip(teachers, numbers, strict=True):
        shared = SHARED_AREAS[: 1 + number % 3]
        extra = OTHER_AREAS[number % 3]
        for name in (*shared, extra):
            links.append(
                Teacher.areas_of_interest.through(
                    teacher_id=teacher.pk, areaofinterest_id=areas[name].id
                )
            )
    Teacher.areas_of_interest.through.objects.bulk_create(links)


def _create_students_and_advisings(numbers: list[int], course: Course) -> None:
    areas = _areas(SHARED_AREAS)
    users = _create_users(
        "aluno",
        numbers,
        lambda n: f"{STUDENT_FIRST_NAMES[(n - 1) % 5]} {STUDENT_SURNAMES[(n - 1) // 5]}",
        UserType.STUDENT,
    )
    students = Student.objects.bulk_create(
        [
            Student(user=user, course=course, registration_number=f"2024VOL{n:03d}")
            for user, n in zip(users, numbers, strict=True)
        ]
    )
    Student.areas_of_interest.through.objects.bulk_create(
        [
            Student.areas_of_interest.through(
                student_id=s.pk, areaofinterest_id=areas[SHARED_AREAS[n % 3]].id
            )
            for s, n in zip(students, numbers, strict=True)
        ]
    )
    teacher = demo_advisor()
    now = timezone.now()
    advisings = [
        Advising(
            student=student,
            teacher=teacher,
            status=AdvisingStatus.ENDED,
            ended_at=now - timedelta(days=number),
            end_reason=END_REASONS[number % len(END_REASONS)],
        )
        for student, number in zip(students, numbers, strict=True)
    ]
    created = Advising.objects.bulk_create(advisings)
    for advising in created:
        advising.started_at = advising.ended_at - ADVISING_LENGTH
    Advising.objects.bulk_update(created, ["started_at"])


@transaction.atomic
def seed_volume() -> tuple[int, int]:
    """Adds the missing volume data; returns how many teachers and ended advisings it made."""
    course = Course.objects.get(name=OWN_COURSE)
    teacher_numbers = _missing("prof", VOLUME_TEACHERS)
    student_numbers = _missing("aluno", VOLUME_ADVISINGS)
    if teacher_numbers:
        _create_teachers(teacher_numbers, course)
    if student_numbers:
        _create_students_and_advisings(student_numbers, course)
    return len(teacher_numbers), len(student_numbers)
