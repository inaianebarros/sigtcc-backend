"""The administrator lists and deactivates users through the Django Admin (RFADM07-08, RN19)."""

from unittest.mock import patch

import pytest
from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.messages import get_messages
from ninja_jwt.token_blacklist.models import BlacklistedToken
from ninja_jwt.tokens import RefreshToken

from apps.accounts.models import Institute, User, UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import AdvisingStatus
from apps.meetings.models import Meeting, MeetingStatus
from config.errors import ConflictError
from tests.factories import (
    make_activity,
    make_admin,
    make_advising,
    make_course,
    make_meeting,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

USERS = "/admin/accounts/user/"
ACTION = "deactivate_user"


def select(*users, **extra):
    return {
        "action": ACTION,
        helpers.ACTION_CHECKBOX_NAME: [user.pk for user in users],
        **extra,
    }


def listed(admin_client, **filters):
    response = admin_client.get(USERS, filters)
    assert response.status_code == 200
    return {user.email for user in response.context["cl"].result_list}


def messages_of(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


def test_the_listing_shows_name_email_profile_and_situation(admin_client):
    student = make_student(full_name="Ana Aluna")
    teacher = make_teacher(full_name="Paulo Professor")
    inactive = make_user(UserType.STUDENT, full_name="Inês Inativa", is_active=False)

    response = admin_client.get(USERS)

    page = response.content.decode()
    assert response.status_code == 200
    for expected in (
        "Ana Aluna",
        student.user.email,
        "Aluno",
        "Paulo Professor",
        teacher.user.email,
        "Professor",
        "Inês Inativa",
        "Ativo",
        "Desativado",
    ):
        assert expected in page
    assert inactive.email in page


def test_the_listing_filters_by_profile(admin_client):
    student = make_student()
    teacher = make_teacher()

    assert listed(admin_client, profile="student") >= {student.user.email}
    assert teacher.user.email not in listed(admin_client, profile="student")
    assert teacher.user.email in listed(admin_client, profile="teacher")
    assert student.user.email not in listed(admin_client, profile="teacher")


def test_the_listing_filters_by_course_for_students_and_teachers(admin_client):
    course, other_course = make_course(), make_course()
    student = make_student(course=course)
    other_student = make_student(course=other_course)
    teacher = make_teacher(courses=[course, other_course])
    outsider = make_teacher(courses=[other_course])

    emails = listed(admin_client, course=course.pk)

    assert emails == {student.user.email, teacher.user.email}
    assert other_student.user.email not in emails
    assert outsider.user.email not in emails


def test_the_listing_filters_by_institute_without_repeating_a_teacher(admin_client):
    institute = Institute.objects.create(name="Instituto de Teste")
    first, second = make_course(institute), make_course(institute)
    elsewhere = make_course()
    student = make_student(course=first)
    teacher = make_teacher(courses=[first, second])
    outsider = make_student(course=elsewhere)

    response = admin_client.get(USERS, {"institute": institute.pk})

    emails = [user.email for user in response.context["cl"].result_list]
    assert sorted(emails) == sorted([student.user.email, teacher.user.email])
    assert outsider.user.email not in emails


def test_the_action_asks_for_confirmation_before_deactivating(admin_client):
    student = make_student()

    response = admin_client.post(USERS, select(student.user))

    assert response.status_code == 200
    assert student.user.full_name in response.content.decode()
    assert User.objects.get(pk=student.user.pk).is_active


def test_confirming_deactivates_the_user_and_ends_the_bonds(admin_client):
    advising = make_advising()
    activity = make_activity(advising)
    meeting = make_meeting(advising, status=MeetingStatus.ACCEPTED)
    refresh = RefreshToken.for_user(advising.student.user)

    response = admin_client.post(USERS, select(advising.student.user, post="yes"))

    assert response.status_code == 302
    assert not User.objects.get(pk=advising.student.user_id).is_active
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ENDED
    assert Activity.objects.get(pk=activity.pk).status == ActivityStatus.ARCHIVED
    assert Meeting.objects.get(pk=meeting.pk).status == MeetingStatus.CANCELLED
    assert BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()


def test_deactivating_a_teacher_ends_the_advisings_of_the_teacher(admin_client):
    advising = make_advising()

    admin_client.post(USERS, select(advising.teacher.user, post="yes"))

    advising.refresh_from_db()
    assert not User.objects.get(pk=advising.teacher.user_id).is_active
    assert advising.status == AdvisingStatus.ENDED


def test_the_action_takes_one_user_at_a_time(admin_client):
    first, second = make_student(), make_teacher()

    confirmation = admin_client.post(USERS, select(first.user, second.user))
    executed = admin_client.post(USERS, select(first.user, second.user, post="yes"))

    for response in (confirmation, executed):
        assert response.status_code == 302
        assert messages_of(response)
    assert User.objects.get(pk=first.user.pk).is_active
    assert User.objects.get(pk=second.user.pk).is_active


def test_an_administrator_cannot_be_deactivated(admin_client):
    other_admin = make_admin(email="outro@example.com")

    response = admin_client.post(USERS, select(other_admin, post="yes"))

    assert response.status_code == 302
    assert messages_of(response)
    assert User.objects.get(pk=other_admin.pk).is_active


def test_an_already_deactivated_user_is_reported_before_asking_for_confirmation(admin_client):
    inactive = make_user(UserType.STUDENT, is_active=False)

    for extra in ({}, {"post": "yes"}):
        response = admin_client.post(USERS, select(inactive, **extra))

        assert response.status_code == 302
        assert any("desativado" in message for message in messages_of(response))


def test_a_conflict_found_while_deactivating_is_reported(admin_client):
    student = make_student()

    with patch(
        "apps.accounts.admin.deactivate_user", side_effect=ConflictError("Conflito de teste.")
    ):
        response = admin_client.post(USERS, select(student.user, post="yes"))

    assert response.status_code == 302
    assert "Conflito de teste." in messages_of(response)


def test_is_active_is_declared_read_only_in_the_admin():
    user_admin = admin.site._registry[User]

    assert "is_active" in user_admin.readonly_fields


def test_a_failure_in_the_cascade_is_reported_and_leaves_the_user_active(admin_client):
    advising = make_advising()

    with patch("apps.accounts.deactivation.close_advising", side_effect=RuntimeError("falha")):
        response = admin_client.post(USERS, select(advising.student.user, post="yes"))

    assert response.status_code == 302
    assert messages_of(response)
    assert User.objects.get(pk=advising.student.user_id).is_active
    advising.refresh_from_db()
    assert advising.status == AdvisingStatus.ACTIVE


def test_is_active_cannot_be_changed_by_the_form(admin_client):
    student = make_student()
    change_url = f"{USERS}{student.user.pk}/change/"

    page = admin_client.get(change_url)
    response = admin_client.post(
        change_url,
        {"full_name": "Outro Nome", "email": "outro@example.com", "is_active": ""},
    )

    assert page.status_code == 200
    assert "is_active" not in page.context["adminform"].form.fields
    assert response.status_code == 403
    user = User.objects.get(pk=student.user.pk)
    assert user.is_active
    assert user.email == student.user.email


def test_a_user_cannot_be_created_or_deleted_through_the_admin(admin_client):
    student = make_student()

    add = admin_client.get(f"{USERS}add/")
    delete = admin_client.post(f"{USERS}{student.user.pk}/delete/", {"post": "yes"})
    listing = admin_client.get(USERS)

    assert add.status_code == 403
    assert delete.status_code == 403
    assert User.objects.filter(pk=student.user.pk).exists()
    actions = dict(listing.context["action_form"].fields["action"].choices)
    assert "delete_selected" not in actions
    assert ACTION in actions
    assert actions[ACTION] == "Excluir (desativar) usuário"


@pytest.mark.parametrize("user_type", [UserType.STUDENT, UserType.TEACHER])
def test_a_student_or_a_teacher_does_not_reach_the_users_admin(client, user_type):
    client.force_login(make_user(user_type))
    victim = make_student()

    responses = [client.get(USERS), client.post(USERS, select(victim.user, post="yes"))]

    for response in responses:
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]
    assert User.objects.get(pk=victim.user.pk).is_active
