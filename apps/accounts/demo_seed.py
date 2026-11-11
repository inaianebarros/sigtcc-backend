"""The demonstration users of every profile, created through the real registration.

Local use only: the users share a known password (ADR 0005).
"""

from dataclasses import dataclass

from django.db import transaction

from .models import AreaOfInterest, Course, Institute, User
from .schemas import StudentRegistrationIn, TeacherRegistrationIn
from .services import register_student, register_teacher

DEMO_DOMAIN = "demo.ufopa.local"
DEMO_PASSWORD = "Senha-Demo-2026"
ADMIN_SLUG = "admin"
ADMIN_FULL_NAME = "Administrador de Demonstração"
STUDENT_REGISTRATION_PREFIX = "2024DEMO"
TEACHER_REGISTRATION_PREFIX = "PDEMO"

INSTITUTE = "Instituto de Engenharia e Geociências"
OWN_COURSE = "Ciência da Computação"
OTHER_COURSE = "Sistemas de Informação"
CATALOG_HINT = "Carregue o catálogo antes: python manage.py loaddata fixtures/seed.json"


class DemoSeedError(Exception):
    pass


@dataclass(frozen=True)
class DemoStudent:
    slug: str
    full_name: str
    areas: tuple[str, ...]
    course: str = OWN_COURSE


@dataclass(frozen=True)
class DemoTeacher:
    slug: str
    full_name: str
    areas: tuple[str, ...]
    course: str = OWN_COURSE
    is_available: bool = True


STUDENTS = (
    DemoStudent(
        "aluno1.sem-orientacao",
        "Ana Beatriz Souza",
        ("Inteligência Artificial", "Aprendizado de Máquina", "Ciência de Dados"),
    ),
    DemoStudent(
        "aluno2.solicitacao-pendente",
        "Bruno Carvalho",
        ("Engenharia de Software", "Desenvolvimento Web"),
    ),
    DemoStudent(
        "aluno3.solicitacao-recusada",
        "Camila Ribeiro",
        ("Redes de Computadores", "Segurança da Informação"),
    ),
    DemoStudent(
        "aluno4.solicitacao-nao-respondida",
        "Diego Martins",
        ("Banco de Dados", "Ciência de Dados"),
    ),
    DemoStudent(
        "aluno5.orientacao-completa",
        "Elisa Nogueira",
        ("Engenharia de Software", "Banco de Dados", "Desenvolvimento Web"),
    ),
    DemoStudent(
        "aluno6.orientacao-vazia",
        "Felipe Araújo",
        ("Inteligência Artificial", "Computação Móvel"),
    ),
    DemoStudent(
        "aluno7.orientacao-encerrada",
        "Gabriela Torres",
        ("Sistemas Distribuídos", "Computação em Nuvem"),
    ),
    DemoStudent(
        "aluno8.outro-curso",
        "Henrique Lima",
        ("Interação Humano-Computador",),
        course=OTHER_COURSE,
    ),
)

TEACHERS = (
    DemoTeacher(
        "prof1.orientandos",
        "Carlos Eduardo Almeida",
        (
            "Engenharia de Software",
            "Banco de Dados",
            "Inteligência Artificial",
            "Desenvolvimento Web",
            "Redes de Computadores",
        ),
    ),
    DemoTeacher(
        "prof2.sem-orientandos",
        "Marina Duarte",
        ("Ciência de Dados", "Aprendizado de Máquina"),
    ),
    DemoTeacher(
        "prof3.indisponivel",
        "Roberto Pinheiro",
        ("Segurança da Informação", "Redes de Computadores"),
        is_available=False,
    ),
    DemoTeacher(
        "prof4.outro-curso",
        "Sônia Falcão",
        ("Interação Humano-Computador", "Informática na Educação"),
        course=OTHER_COURSE,
    ),
)


def demo_email(slug: str) -> str:
    return f"{slug}@{DEMO_DOMAIN}"


def _exists(slug: str) -> bool:
    return User.objects.filter(email__iexact=demo_email(slug)).exists()


@dataclass(frozen=True)
class Catalog:
    institute: Institute
    courses: dict[str, Course]
    areas: dict[str, AreaOfInterest]

    def area_ids(self, names: tuple[str, ...]) -> list[int]:
        return [self.areas[name].id for name in names]


def _catalog() -> Catalog:
    institute = Institute.objects.filter(name=INSTITUTE).first()
    own_course = Course.objects.filter(institute=institute, name=OWN_COURSE).first()
    if institute is None or own_course is None:
        raise DemoSeedError(f"O instituto e o curso do catálogo não existem. {CATALOG_HINT}")
    other_course, _ = Course.objects.get_or_create(institute=institute, name=OTHER_COURSE)
    areas = {area.name: area for area in AreaOfInterest.objects.all()}
    needed = {name for person in (*STUDENTS, *TEACHERS) for name in person.areas}
    if needed - areas.keys():
        raise DemoSeedError(f"Faltam áreas de interesse no catálogo. {CATALOG_HINT}")
    return Catalog(institute, {OWN_COURSE: own_course, OTHER_COURSE: other_course}, areas)


def _create_student(number: int, person: DemoStudent, catalog: Catalog) -> None:
    register_student(
        StudentRegistrationIn(
            registration_number=f"{STUDENT_REGISTRATION_PREFIX}{number:02d}",
            full_name=person.full_name,
            email=demo_email(person.slug),
            password=DEMO_PASSWORD,
            password_confirmation=DEMO_PASSWORD,
            institute_id=catalog.institute.id,
            course_id=catalog.courses[person.course].id,
            area_ids=catalog.area_ids(person.areas),
        )
    )


def _create_teacher(number: int, person: DemoTeacher, catalog: Catalog) -> None:
    teacher = register_teacher(
        TeacherRegistrationIn(
            registration_number=f"{TEACHER_REGISTRATION_PREFIX}{number:02d}",
            full_name=person.full_name,
            email=demo_email(person.slug),
            password=DEMO_PASSWORD,
            password_confirmation=DEMO_PASSWORD,
            institute_id=catalog.institute.id,
            course_ids=[catalog.courses[person.course].id],
            area_ids=catalog.area_ids(person.areas),
        )
    )
    if not person.is_available:
        teacher.is_available = False
        teacher.save(update_fields=["is_available"])


@transaction.atomic
def seed_demo_users() -> list[tuple[str, bool]]:
    """Creates the missing demonstration users; returns (e-mail, created) for each of them."""
    catalog = _catalog()
    report = []
    for number, student in enumerate(STUDENTS, start=1):
        created = not _exists(student.slug)
        if created:
            _create_student(number, student, catalog)
        report.append((demo_email(student.slug), created))
    for number, teacher in enumerate(TEACHERS, start=1):
        created = not _exists(teacher.slug)
        if created:
            _create_teacher(number, teacher, catalog)
        report.append((demo_email(teacher.slug), created))
    created = not _exists(ADMIN_SLUG)
    if created:
        User.objects.create_superuser(
            email=demo_email(ADMIN_SLUG), password=DEMO_PASSWORD, full_name=ADMIN_FULL_NAME
        )
    report.append((demo_email(ADMIN_SLUG), created))
    return report
