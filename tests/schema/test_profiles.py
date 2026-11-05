import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.accounts.models import Course, Institute, PublishedArticle
from tests.factories import make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db


def test_student_registration_number_must_be_unique():
    make_student(registration_number="2024001")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_student(registration_number="2024001")


def test_teacher_registration_number_must_be_unique():
    make_teacher(registration_number="P0001")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_teacher(registration_number="P0001")


def test_student_and_teacher_may_share_a_registration_number():
    make_student(registration_number="12345")

    make_teacher(registration_number="12345")


def test_teacher_starts_available_to_advise():
    teacher = make_teacher()

    assert teacher.is_available is True


def test_course_name_is_unique_within_an_institute():
    institute = Institute.objects.create(name="IEG")
    Course.objects.create(institute=institute, name="Ciência da Computação")

    with pytest.raises(IntegrityError), transaction.atomic():
        Course.objects.create(institute=institute, name="Ciência da Computação")


def test_published_article_requires_an_owner():
    with pytest.raises(IntegrityError), transaction.atomic():
        PublishedArticle.objects.create(title="Sem dono", year=2024)


def test_published_article_cannot_belong_to_a_student_and_a_teacher():
    with pytest.raises(IntegrityError), transaction.atomic():
        PublishedArticle.objects.create(
            title="Dois donos", year=2024, student=make_student(), teacher=make_teacher()
        )


def test_published_article_may_belong_to_a_single_profile():
    article = PublishedArticle.objects.create(title="De aluno", year=2024, student=make_student())

    assert article.teacher is None


@pytest.mark.parametrize("owner", ["student", "teacher"])
def test_a_profile_cannot_repeat_an_article_with_the_same_title_and_year(owner):
    profile = make_student() if owner == "student" else make_teacher()
    PublishedArticle.objects.create(title="Redes Neurais", year=2024, **{owner: profile})

    with pytest.raises(IntegrityError), transaction.atomic():
        PublishedArticle.objects.create(title="  REDES   neurais ", year=2024, **{owner: profile})
    with pytest.raises(IntegrityError), transaction.atomic():
        PublishedArticle.objects.create(title="Rédes Neuráis", year=2024, **{owner: profile})


def test_the_same_title_is_allowed_in_another_year_or_for_another_profile():
    student = make_student()
    PublishedArticle.objects.create(title="Redes Neurais", year=2024, student=student)

    PublishedArticle.objects.create(title="Redes Neurais", year=2025, student=student)
    PublishedArticle.objects.create(title="Redes Neurais", year=2024, student=make_student())
    PublishedArticle.objects.create(title="Redes Neurais", year=2024, teacher=make_teacher())


def test_a_course_in_use_cannot_be_deleted():
    course = make_course()
    make_student(course=course)

    with pytest.raises(ProtectedError):
        course.delete()
