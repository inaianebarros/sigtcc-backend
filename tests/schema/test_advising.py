import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
    ThesisTopic,
)
from tests.factories import make_student, make_teacher

pytestmark = pytest.mark.django_db


def test_student_cannot_have_two_pending_requests():
    student, teacher = make_student(), make_teacher()
    AdvisingRequest.objects.create(student=student, teacher=teacher)

    with pytest.raises(IntegrityError), transaction.atomic():
        AdvisingRequest.objects.create(student=student, teacher=make_teacher())


def test_student_may_request_again_after_the_previous_request_was_answered():
    student = make_student()
    first = AdvisingRequest.objects.create(student=student, teacher=make_teacher())
    first.status = AdvisingRequestStatus.REJECTED
    first.save()

    AdvisingRequest.objects.create(student=student, teacher=make_teacher())

    assert student.advising_requests.count() == 2


def test_different_students_may_each_have_a_pending_request():
    teacher = make_teacher()
    AdvisingRequest.objects.create(student=make_student(), teacher=teacher)

    AdvisingRequest.objects.create(student=make_student(), teacher=teacher)

    assert teacher.advising_requests.count() == 2


def test_student_cannot_have_two_active_advisings():
    student = make_student()
    Advising.objects.create(student=student, teacher=make_teacher())

    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(student=student, teacher=make_teacher())


def test_student_may_start_a_new_advising_after_the_previous_one_ended():
    student = make_student()
    first = Advising.objects.create(student=student, teacher=make_teacher())
    first.status = AdvisingStatus.ENDED
    first.ended_at = timezone.now()
    first.end_reason = "Concluída."
    first.save()

    Advising.objects.create(student=student, teacher=make_teacher())

    assert student.advisings.count() == 2


def test_ended_advising_must_have_an_end_date_and_active_must_not():
    student, teacher = make_student(), make_teacher()

    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(
            student=student, teacher=teacher, status=AdvisingStatus.ENDED, end_reason="Concluída."
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(student=student, teacher=teacher, ended_at=timezone.now())


def test_an_ended_advising_needs_a_reason_and_an_active_one_must_not_have_it():
    student, teacher = make_student(), make_teacher()

    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(
            student=student,
            teacher=teacher,
            status=AdvisingStatus.ENDED,
            ended_at=timezone.now(),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(student=student, teacher=teacher, end_reason="Sem estar encerrada.")


def test_a_request_originates_at_most_one_advising():
    student, teacher = make_student(), make_teacher()
    request = AdvisingRequest.objects.create(
        student=student, teacher=teacher, status=AdvisingRequestStatus.ACCEPTED
    )
    Advising.objects.create(
        student=student,
        teacher=teacher,
        request=request,
        status=AdvisingStatus.ENDED,
        ended_at=timezone.now(),
        end_reason="Concluída.",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        Advising.objects.create(student=student, teacher=teacher, request=request)


def test_student_has_at_most_one_thesis_topic():
    student = make_student()
    ThesisTopic.objects.create(student=student, description="Gestão de TCC")

    with pytest.raises(IntegrityError), transaction.atomic():
        ThesisTopic.objects.create(student=student, description="Outro tema")
