"""Journeys across the accounts, advising and meetings flows (#55).

Each test is one scenario across several endpoints, as a real client would walk it. The
per-feature tests already cover each rule alone; these cover how the rules fit together.
"""

import datetime

import pytest
from django.utils import timezone

from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
)
from apps.advising.services import OTHER_COURSE_MESSAGE
from apps.meetings.models import Meeting, MeetingStatus
from tests.api.helpers import (
    LOGIN_URL,
    LOGOUT_URL,
    ME_URL,
    PASSWORD,
    REFRESH_COOKIE,
    REFRESH_URL,
    bearer,
    inbox,
    post_json,
)
from tests.api.journeys.helpers import (
    answer_meeting,
    answer_request,
    end_advising,
    my_request_status,
    put_topic,
    request_advising,
    request_meeting,
    search_advisors,
)
from tests.factories import (
    make_advising,
    make_area,
    make_course,
    make_student,
    make_teacher,
)

pytestmark = pytest.mark.django_db


def bearer_of(response):
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestRegistrationAndSession:
    """Journey 1: a student and a teacher register, sign in, refresh and sign out."""

    def test_a_student_registers_signs_in_refreshes_and_signs_out(self, browser):
        course = make_course()
        areas = [make_area().id, make_area().id]
        registered = post_json(
            browser,
            "/api/auth/register/aluno",
            {
                "registration_number": "202411001",
                "full_name": "Ana Souza",
                "email": "ana.souza@example.com",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
                "institute_id": course.institute_id,
                "course_id": course.id,
                "area_ids": areas,
            },
        )
        assert registered.status_code == 201

        login = post_json(
            browser, LOGIN_URL, {"email": "ana.souza@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200
        assert login.json()["user"]["type"] == "student"
        first_cookie = login.cookies[REFRESH_COOKIE].value
        me = browser.get(ME_URL, headers=bearer_of(login))
        assert me.status_code == 200
        assert me.json()["has_active_advising"] is False

        refreshed = browser.post(REFRESH_URL)
        assert refreshed.status_code == 200
        assert refreshed.cookies[REFRESH_COOKIE].value != first_cookie
        assert browser.get(ME_URL, headers=bearer_of(refreshed)).status_code == 200

        assert browser.post(LOGOUT_URL).status_code == 204
        assert browser.post(REFRESH_URL).status_code == 401

    def test_a_teacher_registers_signs_in_and_is_listed_for_students_of_the_course(
        self, browser, client
    ):
        course = make_course()
        shared_area = make_area()
        registered = post_json(
            browser,
            "/api/auth/register/professor",
            {
                "registration_number": "P2024001",
                "full_name": "Beatriz Lima",
                "email": "beatriz.lima@example.com",
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
                "institute_id": course.institute_id,
                "course_ids": [course.id],
                "area_ids": [shared_area.id],
            },
        )
        assert registered.status_code == 201

        login = post_json(
            browser, LOGIN_URL, {"email": "beatriz.lima@example.com", "password": PASSWORD}
        )
        assert login.status_code == 200
        assert login.json()["user"]["type"] == "teacher"
        me = browser.get(ME_URL, headers=bearer_of(login))
        assert me.json()["active_advisees_count"] == 0

        student = make_student(course=course, areas=[shared_area])
        names = [item["full_name"] for item in search_advisors(client, student).json()["items"]]
        assert names == ["Beatriz Lima"]

        assert browser.post(LOGOUT_URL).status_code == 204
        assert browser.post(REFRESH_URL).status_code == 401


class TestTheLifeOfAnAdvising:
    """Journey 2: search, request, accept, meet, register the topic and end (RN04, RN34, RF53)."""

    def test_from_the_search_to_the_ending_with_everything_that_follows(self, client):
        course = make_course()
        shared = [make_area(), make_area()]
        student = make_student(course=course, areas=shared, full_name="Ana Souza")
        weak = make_teacher(courses=[course], areas=shared[:1], full_name="Zélia Pouca Afinidade")
        strong = make_teacher(courses=[course], areas=shared, full_name="Carlos Lima")

        # The student searches the advisors: the one with more areas in common comes first.
        results = search_advisors(client, student).json()["items"]
        assert [item["id"] for item in results] == [strong.user_id, weak.user_id]
        assert [item["compatibility"] for item in results] == [2, 1]

        # The request, and RN04: no second request while one is pending.
        created = request_advising(client, student, strong)
        assert created.status_code == 201
        assert request_advising(client, student, weak).status_code == 409

        # The teacher answers and accepts.
        received = client.get("/api/orientacao/solicitacoes", headers=bearer(strong.user)).json()
        assert [item["id"] for item in received["items"]] == [created.json()["id"]]
        assert answer_request(client, strong, created.json()["id"], "accept").status_code == 200

        # The student has an active advising and cannot request again (RN04).
        status = my_request_status(client, student)
        assert status["advising"]["teacher"]["id"] == strong.user_id
        assert status["can_request"] is False
        assert status["blocked_reason"] == "active_advising"
        advising_id = status["advising"]["id"]
        assert request_advising(client, student, weak).status_code == 409

        # Meetings: one the student asks and the teacher accepts, one the teacher sets and the
        # student accepts, and one left pending. All three are in the future.
        asked = request_meeting(client, student.user, days=3)
        assert asked.status_code == 201
        assert answer_meeting(client, strong.user, asked.json()["id"], "accept").status_code == 200
        set_by_teacher = request_meeting(client, strong.user, days=5, student=student)
        assert set_by_teacher.status_code == 201
        assert (
            answer_meeting(client, student.user, set_by_teacher.json()["id"], "accept").status_code
            == 200
        )
        pending = request_meeting(client, student.user, days=7)
        assert pending.status_code == 201

        # The thesis topic belongs to the advising and is readable by both sides.
        assert put_topic(client, student).status_code == 201
        assert (
            client.get("/api/tcc/tema", headers=bearer(student.user)).json()["description"]
            == "Redes neurais aplicadas à saúde"
        )
        topic_for_teacher = client.get(
            f"/api/tcc/orientandos/{student.user_id}/tema", headers=bearer(strong.user)
        )
        assert topic_for_teacher.json()["description"] == "Redes neurais aplicadas à saúde"

        # The teacher ends it with a reason (RN07, RN30, RN33).
        ended = end_advising(client, strong, advising_id)
        assert ended.status_code == 200
        assert ended.json()["status"] == "ended"

        # RN34: the future meetings, accepted or pending, are cancelled by the system.
        meetings = Meeting.objects.filter(advising_id=advising_id)
        assert meetings.count() == 3
        assert set(meetings.values_list("status", flat=True)) == {MeetingStatus.CANCELLED}
        assert all(meeting.cancelled_by_system for meeting in meetings)

        # RF53: the student gets one notification of the ending, with the counts.
        ending = [item for item in inbox(client, student.user) if item["type"] == "advising_ended"]
        assert len(ending) == 1
        assert ending[0]["payload"]["cancelled_meetings"] == 3
        assert ending[0]["payload"]["archived_activities"] == 0
        assert sorted(item["type"] for item in inbox(client, strong.user)) == [
            "advising_request_received",
            "meeting_answered",
            "meeting_requested",
            "meeting_requested",
        ]
        assert sorted(item["type"] for item in inbox(client, student.user)) == [
            "advising_ended",
            "advising_request_answered",
            "meeting_answered",
            "meeting_requested",
        ]

        # The student is freed: no advising, may request again; the topic is frozen (RN12).
        freed = my_request_status(client, student)
        assert freed["advising"] is None
        assert freed["can_request"] is True
        assert put_topic(client, student, "Outro tema").status_code == 409
        assert client.get("/api/orientacao/orientandos", headers=bearer(strong.user)).json() == {
            "items": [],
            "count": 0,
        }
        assert request_advising(client, student, weak).status_code == 201


class TestRefusalAndUnansweredRequests:
    """Journey 3: a refusal, a new request to the same teacher, and the 7 days (RN05, RN20)."""

    def test_after_a_refusal_the_student_requests_again_and_an_unanswered_one_frees_them(
        self, client
    ):
        course = make_course()
        student = make_student(course=course)
        teacher = make_teacher(courses=[course])

        first = request_advising(client, student, teacher)
        assert first.status_code == 201
        assert answer_request(client, teacher, first.json()["id"], "reject").status_code == 200
        refused = my_request_status(client, student)
        assert refused["request"]["status"] == "rejected"
        assert refused["can_request"] is True

        # RN05: after a refusal the student asks the same teacher again.
        second = request_advising(client, student, teacher)
        assert second.status_code == 201
        assert my_request_status(client, student)["can_request"] is False

        # RN20: after 7 days without an answer it reads as unanswered and frees the student.
        # (The refused one is made older still, so the unanswered one is the latest request.)
        AdvisingRequest.objects.filter(pk=first.json()["id"]).update(
            requested_at=timezone.now() - datetime.timedelta(days=20)
        )
        AdvisingRequest.objects.filter(pk=second.json()["id"]).update(
            requested_at=timezone.now() - datetime.timedelta(days=8)
        )
        unanswered = my_request_status(client, student)
        assert unanswered["request"]["status"] == "unanswered"
        assert unanswered["can_request"] is True
        too_late = answer_request(client, teacher, second.json()["id"], "accept")
        assert too_late.status_code == 409
        assert too_late.json() == {"detail": "O prazo para responder esta solicitação terminou."}
        assert not Advising.objects.exists()

        third = request_advising(client, student, teacher)
        assert third.status_code == 201
        assert answer_request(client, teacher, third.json()["id"], "accept").status_code == 200
        assert Advising.objects.get().status == AdvisingStatus.ACTIVE
        statuses = AdvisingRequest.objects.order_by("id").values_list("status", flat=True)
        assert list(statuses) == [
            AdvisingRequestStatus.REJECTED,
            # The overdue one is recorded as unanswered when the next request is created.
            AdvisingRequestStatus.UNANSWERED,
            AdvisingRequestStatus.ACCEPTED,
        ]

    def test_the_answer_of_a_request_is_final_and_cannot_be_given_twice(self, client):
        course = make_course()
        student = make_student(course=course)
        teacher = make_teacher(courses=[course])
        created = request_advising(client, student, teacher)

        assert answer_request(client, teacher, created.json()["id"], "accept").status_code == 200
        again = answer_request(client, teacher, created.json()["id"], "reject")

        assert again.status_code == 409
        assert again.json() == {"detail": "Esta solicitação já foi respondida."}


class TestIsolationBetweenUsers:
    """Journey 4: another teacher or student gets the same 404 everywhere (RN28, RN32)."""

    @staticmethod
    def two_advisings():
        course = make_course()
        owner = make_teacher(courses=[course], full_name="Carlos Lima")
        student = make_student(course=course, full_name="Ana Souza")
        advising = make_advising(student=student, teacher=owner)
        other_teacher = make_teacher(courses=[course], full_name="Outro Professor")
        other_student = make_student(course=course, full_name="Outro Aluno")
        make_advising(student=other_student, teacher=other_teacher)
        return owner, student, advising, other_teacher, other_student

    def test_another_teacher_reaches_nothing_of_a_student_that_is_not_theirs(self, client):
        owner, student, advising, intruder, _ = self.two_advisings()
        meeting = request_meeting(client, student.user, days=3).json()
        topic = put_topic(client, student)
        assert topic.status_code == 201
        headers = bearer(intruder.user)

        responses = {
            "profile": client.get(f"/api/alunos/{student.user_id}/perfil", headers=headers),
            "topic read": client.get(
                f"/api/tcc/orientandos/{student.user_id}/tema", headers=headers
            ),
            "topic write": client.put(
                f"/api/tcc/orientandos/{student.user_id}/tema",
                data={"description": "Tema escrito por um professor que não orienta"},
                content_type="application/json",
                headers=headers,
            ),
            "meeting with the student": request_meeting(
                client, intruder.user, days=4, student=student
            ),
            "answer a meeting": answer_meeting(client, intruder.user, meeting["id"], "accept"),
            "cancel a meeting": post_json(
                client,
                f"/api/reunioes/{meeting['id']}/cancelar",
                {"reason": "Quero cancelar."},
                headers=headers,
            ),
            "edit a meeting": client.patch(
                f"/api/reunioes/{meeting['id']}",
                data={"description": "Editada por outro"},
                content_type="application/json",
                headers=headers,
            ),
            "end the advising": end_advising(client, intruder, advising.id),
        }

        statuses = {name: response.status_code for name, response in responses.items()}
        assert set(statuses.values()) == {404}, statuses
        assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ACTIVE
        assert Meeting.objects.get(pk=meeting["id"]).status == MeetingStatus.PENDING
        assert (
            client.get(
                "/api/tcc/orientandos/" + str(student.user_id) + "/tema", headers=bearer(owner.user)
            ).json()["description"]
            == topic.json()["description"]
        )

    def test_another_student_reaches_nothing_of_a_meeting_that_is_not_theirs(self, client):
        owner, student, _, _, intruder = self.two_advisings()
        meeting = request_meeting(client, student.user, days=3).json()
        headers = bearer(intruder.user)

        statuses = {
            "answer": answer_meeting(client, intruder.user, meeting["id"], "accept").status_code,
            "cancel": post_json(
                client,
                f"/api/reunioes/{meeting['id']}/cancelar",
                {"reason": "Quero cancelar."},
                headers=headers,
            ).status_code,
            "edit": client.patch(
                f"/api/reunioes/{meeting['id']}",
                data={"description": "Editada por outro"},
                content_type="application/json",
                headers=headers,
            ).status_code,
        }

        assert set(statuses.values()) == {404}, statuses
        listed = client.get("/api/reunioes", headers=headers).json()
        assert meeting["id"] not in [item["id"] for item in listed["items"]]
        assert answer_meeting(client, owner.user, meeting["id"], "accept").status_code == 200

    def test_the_list_of_requests_and_of_advisees_shows_only_the_own(self, client):
        owner, student, advising, intruder, _ = self.two_advisings()
        stranger = make_student(course=student.course)
        request = request_advising(client, stranger, owner)
        assert request.status_code == 201

        assert client.get("/api/orientacao/solicitacoes", headers=bearer(intruder.user)).json() == {
            "items": [],
            "count": 0,
        }
        assert answer_request(client, intruder, request.json()["id"], "accept").status_code == 404
        advisees = client.get("/api/orientacao/orientandos", headers=bearer(intruder.user)).json()
        assert student.user_id not in [item["student"]["id"] for item in advisees["items"]]

    def test_a_student_of_another_course_does_not_see_the_teacher(self, client):
        course = make_course()
        teacher = make_teacher(courses=[course])
        outsider = make_student(course=make_course())

        assert search_advisors(client, outsider).json() == {"items": [], "count": 0}
        profile = client.get(
            f"/api/professores/{teacher.user_id}/perfil", headers=bearer(outsider.user)
        )
        assert profile.status_code == 404
        # RN28: asking a teacher of another course is refused with the documented message.
        refused = request_advising(client, outsider, teacher)
        assert refused.status_code == 409
        assert refused.json() == {"detail": OTHER_COURSE_MESSAGE}
        assert not AdvisingRequest.objects.exists()

    def test_the_notifications_of_a_user_are_not_reachable_by_another(self, client):
        owner, student, _, intruder, other_student = self.two_advisings()
        request_meeting(client, student.user, days=3)
        mine = inbox(client, owner.user)
        assert len(mine) == 1

        stolen = client.patch(
            f"/api/notificacoes/{mine[0]['id']}/lida", headers=bearer(intruder.user)
        )

        assert stolen.status_code == 404
        assert inbox(client, intruder.user) == []
        assert inbox(client, other_student.user) == []
        assert inbox(client, owner.user)[0]["is_read"] is False
