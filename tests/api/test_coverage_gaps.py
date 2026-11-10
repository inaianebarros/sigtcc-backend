"""Tests for the gaps the coverage report found in accounts and advising (#55).

Each one covers a rule that no earlier test reached: a rate-limit store that fails, a logout with
a broken cookie, a refresh token used twice at once, two requests that pass the same check, a topic
written as the advising ends, and the representations of the account models.
"""

from unittest.mock import patch

import pytest
from django.db import IntegrityError

from apps.accounts.models import (
    AreaOfInterest,
    Course,
    Institute,
    PublishedArticle,
    User,
    UserType,
)
from apps.advising.models import Advising, AdvisingRequest, AdvisingStatus
from apps.advising.services import PENDING_REQUEST_MESSAGE, close_advising
from apps.advising.thesis import NO_ACTIVE_ADVISING_MESSAGE, write_topic
from config.errors import ConflictError
from tests.api.helpers import (
    LOGIN_URL,
    LOGOUT_URL,
    REFRESH_COOKIE,
    REFRESH_URL,
    bearer,
    post_json,
)
from tests.factories import (
    DEFAULT_PASSWORD,
    make_advising,
    make_area,
    make_course,
    make_student,
    make_teacher,
    make_user,
)

pytestmark = pytest.mark.django_db

CACHE_DOWN = ConnectionError("cache fora do ar")


def login(browser, password=DEFAULT_PASSWORD):
    return post_json(browser, LOGIN_URL, {"email": "ana@example.com", "password": password})


class TestARateLimitStoreThatFails:
    """The failure counters fail open: a broken store never takes the login down (ADR 0003)."""

    def test_a_failing_check_before_the_attempt_lets_the_login_through(self, browser):
        make_user(email="ana@example.com")

        with patch("config.rate_limit.peek", side_effect=CACHE_DOWN):
            response = login(browser)

        assert response.status_code == 200

    def test_a_failing_count_of_a_wrong_password_still_answers_the_generic_401(self, browser):
        make_user(email="ana@example.com")

        with patch("config.rate_limit.hit", side_effect=CACHE_DOWN):
            response = login(browser, password="Senha-Errada-2026")

        assert response.status_code == 401

    def test_a_failing_reset_after_a_successful_login_does_not_undo_the_login(self, browser):
        make_user(email="ana@example.com")

        with patch("config.rate_limit.clear", side_effect=CACHE_DOWN):
            response = login(browser)

        assert response.status_code == 200


class TestTheSessionEdgeCases:
    def test_a_logout_with_a_broken_cookie_still_succeeds_and_clears_it(self, browser):
        browser.cookies[REFRESH_COOKIE] = "isto-nao-e-um-token"

        response = browser.post(LOGOUT_URL)

        assert response.status_code == 204
        assert response.cookies[REFRESH_COOKIE].value == ""

    def test_a_refresh_token_that_another_request_used_first_is_refused(self, browser):
        make_user(email="ana@example.com")
        login(browser)

        # Another request blacklisted the token between the checks and this one.
        with patch("ninja_jwt.tokens.RefreshToken.blacklist", return_value=(None, False)):
            response = browser.post(REFRESH_URL)

        assert response.status_code == 401


class TestAdvisingEdgeCases:
    def test_two_requests_that_pass_the_same_check_leave_only_one_and_answer_409(self, client):
        course = make_course()
        student = make_student(course=course)
        teacher = make_teacher(courses=[course])

        # The unique constraint is the last guard: it fires after both passed the earlier check.
        with patch.object(AdvisingRequest.objects, "create", side_effect=IntegrityError("dup")):
            response = post_json(
                client,
                "/api/orientacao/solicitacoes",
                {"teacher_id": teacher.user_id},
                headers=bearer(student.user),
            )

        assert response.status_code == 409
        assert response.json() == {"detail": PENDING_REQUEST_MESSAGE}
        assert not AdvisingRequest.objects.exists()

    def test_a_topic_written_as_the_advising_ends_is_refused(self):
        student = make_student()
        advising = make_advising(student=student)
        stale = Advising.objects.get(pk=advising.pk)
        close_advising(Advising.objects.get(pk=advising.pk), "Encerrada durante a escrita.")
        assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED

        with pytest.raises(ConflictError) as error:
            write_topic(stale, "Um tema que chega tarde demais")

        assert error.value.detail == NO_ACTIVE_ADVISING_MESSAGE


class TestTheAccountModels:
    def test_a_superuser_is_an_administrator_with_staff_access(self):
        admin = User.objects.create_superuser("admin@example.com", "Senha-Segura-2026")

        assert admin.type == UserType.ADMIN
        assert admin.full_name == "Administrador"
        assert admin.is_staff and admin.is_superuser
        assert admin.check_password("Senha-Segura-2026")

    def test_each_model_is_shown_by_its_name(self):
        institute = Institute.objects.create(name="Instituto de Engenharia")
        course = Course.objects.create(institute=institute, name="Ciência da Computação")
        area = AreaOfInterest.objects.create(name="Inteligência Artificial")
        student = make_student(course=course, full_name="Ana Souza", areas=[area])
        teacher = make_teacher(courses=[course], full_name="Carlos Lima", areas=[make_area()])
        article = PublishedArticle.objects.create(student=student, title="Um artigo", year=2025)

        assert str(student.user) == student.user.email
        assert str(institute) == "Instituto de Engenharia"
        assert str(course) == "Ciência da Computação"
        assert str(area) == "Inteligência Artificial"
        assert str(student) == "Ana Souza"
        assert str(teacher) == "Carlos Lima"
        assert str(article) == "Um artigo"
