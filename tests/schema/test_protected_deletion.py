import pytest
from django.db.models import ProtectedError

from .factories import (
    make_activity,
    make_advising,
    make_advising_request,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db


def student_with_request():
    request = make_advising_request()
    return request.student


def teacher_with_advising():
    return make_advising().teacher


def advising_with_activity():
    return make_activity().advising


def user_with_student_profile():
    return make_student().user


def user_with_teacher_profile():
    return make_teacher().user


@pytest.mark.parametrize(
    "make_record_in_use",
    [
        user_with_student_profile,
        user_with_teacher_profile,
        student_with_request,
        teacher_with_advising,
        advising_with_activity,
    ],
)
def test_records_referenced_by_history_cannot_be_deleted(make_record_in_use):
    record = make_record_in_use()

    with pytest.raises(ProtectedError):
        record.delete()
