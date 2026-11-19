import datetime

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN, INSTITUTE, OWN_COURSE
from apps.accounts.models import Course, Institute, Teacher, User
from apps.activities.models import ActivityStatus, Review, ReviewDecision
from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
)
from apps.meetings.models import MeetingStatus
from apps.personas.simulated_teacher import (
    FIRST_REVIEW_JUSTIFICATION,
    SIMULATED_TEACHER_EMAIL,
    SIMULATED_TEACHER_NAME,
    AcceptAdvisingRequests,
    AcceptMeetings,
    ReviewActivities,
    SimulatedTeacherProvisioning,
)
from tests.api.helpers import bearer
from tests.factories import (
    make_activity,
    make_advising,
    make_advising_request,
    make_area,
    make_meeting,
    make_student,
    make_user,
)

pytestmark = pytest.mark.django_db

DEMO_STUDENT_EMAIL = f"aluno.simulado@{DEMO_DOMAIN}"


@pytest.fixture(autouse=True)
def personas_enabled(settings):
    settings.PERSONAS_ENABLED = True
    settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 20


@pytest.fixture
def course():
    institute = Institute.objects.create(name=INSTITUTE)
    return Course.objects.create(institute=institute, name=OWN_COURSE)


@pytest.fixture
def areas(course):
    return [make_area(), make_area()]


@pytest.fixture
def teacher(course, areas):
    perform_all(SimulatedTeacherProvisioning())
    return Teacher.objects.get(user__email=SIMULATED_TEACHER_EMAIL)


def perform_all(rule):
    actions = list(rule.pending_actions())
    for action in actions:
        action.perform()
    return actions


def make_demo_student(course):
    student = make_student(course=course)
    student.user.email = DEMO_STUDENT_EMAIL
    student.user.save(update_fields=["email"])
    return student


def age_request(advising_request, seconds):
    AdvisingRequest.objects.filter(pk=advising_request.pk).update(
        requested_at=timezone.now() - datetime.timedelta(seconds=seconds)
    )


def make_in_review_activity(advising):
    activity = make_activity(advising)
    activity.status = ActivityStatus.IN_REVIEW
    activity.save(update_fields=["status"])
    return activity


def make_review(activity, decision=ReviewDecision.REJECTED):
    return Review.objects.create(
        activity=activity,
        decision=decision,
        justification="Ajustar." if decision == ReviewDecision.REJECTED else "",
    )


class TestProvisioning:
    def test_creates_the_teacher_with_the_simulated_label_and_the_course(self, course, areas):
        perform_all(SimulatedTeacherProvisioning())

        teacher = Teacher.objects.get(user__email=SIMULATED_TEACHER_EMAIL)
        assert teacher.user.full_name == SIMULATED_TEACHER_NAME
        assert "simulado" in teacher.user.full_name.lower()
        assert teacher.is_available is True
        assert list(teacher.courses.all()) == [course]
        assert set(teacher.areas_of_interest.all()) == set(areas)

    def test_running_twice_does_not_duplicate_the_teacher(self, course, areas):
        perform_all(SimulatedTeacherProvisioning())
        second_run = perform_all(SimulatedTeacherProvisioning())

        assert second_run == []
        assert Teacher.objects.filter(user__email=SIMULATED_TEACHER_EMAIL).count() == 1

    def test_restores_availability_and_new_catalog_areas(self, teacher):
        Teacher.objects.filter(pk=teacher.pk).update(is_available=False)
        new_area = make_area()

        perform_all(SimulatedTeacherProvisioning())

        teacher.refresh_from_db()
        assert teacher.is_available is True
        assert new_area in teacher.areas_of_interest.all()

    def test_does_nothing_without_the_course_catalog(self, db):
        assert perform_all(SimulatedTeacherProvisioning()) == []
        assert not User.objects.filter(email=SIMULATED_TEACHER_EMAIL).exists()

    def test_appears_in_the_search_of_a_computer_science_student(self, client, teacher, course):
        student = make_student(course=course, areas=[teacher.areas_of_interest.first()])

        response = client.get("/api/orientadores/busca", headers=bearer(student.user))

        assert response.status_code == 200
        assert SIMULATED_TEACHER_NAME in [item["full_name"] for item in response.json()["items"]]


class TestAcceptAdvisingRequests:
    def test_accepts_a_request_older_than_thirty_seconds(self, teacher, course):
        advising_request = make_advising_request(make_student(course=course), teacher)
        age_request(advising_request, 31)

        actions = perform_all(AcceptAdvisingRequests())

        assert len(actions) == 1
        advising_request.refresh_from_db()
        assert advising_request.status == AdvisingRequestStatus.ACCEPTED
        assert Advising.objects.filter(request=advising_request, teacher=teacher).exists()

    def test_waits_for_a_request_younger_than_thirty_seconds(self, teacher, course):
        advising_request = make_advising_request(make_student(course=course), teacher)
        age_request(advising_request, 10)

        assert perform_all(AcceptAdvisingRequests()) == []
        advising_request.refresh_from_db()
        assert advising_request.status == AdvisingRequestStatus.PENDING

    def test_ignores_requests_that_are_answered_or_overdue(self, teacher, course):
        answered = make_advising_request(make_student(course=course), teacher)
        AdvisingRequest.objects.filter(pk=answered.pk).update(status=AdvisingRequestStatus.REJECTED)
        overdue = make_advising_request(make_student(course=course), teacher)
        age_request(overdue, 8 * 24 * 3600)

        assert perform_all(AcceptAdvisingRequests()) == []

    def test_ignores_a_request_addressed_to_another_teacher(self, teacher, course):
        other_teacher = make_advising_request().teacher
        advising_request = make_advising_request(make_student(course=course), other_teacher)
        age_request(advising_request, 60)

        assert perform_all(AcceptAdvisingRequests()) == []

    def test_ignores_a_student_of_the_simulated_domain(self, teacher, course):
        advising_request = make_advising_request(make_demo_student(course), teacher)
        age_request(advising_request, 60)

        assert perform_all(AcceptAdvisingRequests()) == []

    def test_does_nothing_without_the_simulated_teacher(self, db):
        assert perform_all(AcceptAdvisingRequests()) == []


class TestReviewActivities:
    def test_refuses_the_first_review_with_the_standard_justification(self, teacher, course):
        activity = make_in_review_activity(make_advising(make_student(course=course), teacher))

        actions = perform_all(ReviewActivities())

        assert len(actions) == 1
        activity.refresh_from_db()
        assert activity.status == ActivityStatus.PENDING
        review = activity.reviews.get()
        assert review.decision == ReviewDecision.REJECTED
        assert review.justification == FIRST_REVIEW_JUSTIFICATION
        assert 1 <= len(FIRST_REVIEW_JUSTIFICATION) <= 500

    def test_accepts_the_following_reviews_and_completes_the_activity(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        make_review(make_activity(advising))
        activity = make_in_review_activity(advising)

        perform_all(ReviewActivities())

        activity.refresh_from_db()
        assert activity.status == ActivityStatus.COMPLETED
        assert activity.reviews.get().decision == ReviewDecision.ACCEPTED

    def test_a_previous_acceptance_also_counts_as_a_review_of_the_advising(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        make_review(make_activity(advising), ReviewDecision.ACCEPTED)
        activity = make_in_review_activity(advising)

        perform_all(ReviewActivities())

        activity.refresh_from_db()
        assert activity.status == ActivityStatus.COMPLETED

    def test_takes_one_activity_per_advising_per_cycle(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        first = make_in_review_activity(advising)
        second = make_in_review_activity(advising)

        assert len(perform_all(ReviewActivities())) == 1
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.status, second.status) == (ActivityStatus.PENDING, ActivityStatus.IN_REVIEW)

        second_cycle = perform_all(ReviewActivities())

        assert len(second_cycle) == 1
        second.refresh_from_db()
        assert second.status == ActivityStatus.COMPLETED

    def test_each_advising_has_its_own_first_review(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        make_review(make_activity(advising))
        fresh = make_in_review_activity(make_advising(make_student(course=course), teacher))

        perform_all(ReviewActivities())

        fresh.refresh_from_db()
        assert fresh.status == ActivityStatus.PENDING

    @pytest.mark.parametrize(
        "status", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.COMPLETED]
    )
    def test_ignores_an_activity_that_is_not_in_review(self, teacher, course, status):
        activity = make_activity(make_advising(make_student(course=course), teacher))
        activity.status = status
        activity.save(update_fields=["status"])

        assert perform_all(ReviewActivities()) == []

    def test_ignores_a_deleted_activity_and_an_ended_advising(self, teacher, course):
        deleted = make_in_review_activity(make_advising(make_student(course=course), teacher))
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at"])
        ended = make_advising(make_student(course=course), teacher)
        make_in_review_activity(ended)
        Advising.objects.filter(pk=ended.pk).update(
            status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada."
        )

        assert perform_all(ReviewActivities()) == []

    def test_ignores_a_student_of_the_simulated_domain(self, teacher, course):
        make_in_review_activity(make_advising(make_demo_student(course), teacher))

        assert perform_all(ReviewActivities()) == []


class TestAcceptMeetings:
    def test_accepts_a_pending_meeting_proposed_by_the_student(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        meeting = make_meeting(advising, author=advising.student.user)

        actions = perform_all(AcceptMeetings())

        assert len(actions) == 1
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.ACCEPTED

    @pytest.mark.parametrize(
        "status", [MeetingStatus.CANCELLED, MeetingStatus.ACCEPTED, MeetingStatus.REJECTED]
    )
    def test_skips_a_meeting_that_is_no_longer_pending(self, teacher, course, status):
        advising = make_advising(make_student(course=course), teacher)
        meeting = make_meeting(advising, status=status, author=advising.student.user)

        assert perform_all(AcceptMeetings()) == []
        meeting.refresh_from_db()
        assert meeting.status == status

    def test_ignores_a_meeting_the_teacher_proposed_or_that_is_over(self, teacher, course):
        advising = make_advising(make_student(course=course), teacher)
        make_meeting(advising, author=teacher.user)
        make_meeting(
            advising,
            author=advising.student.user,
            scheduled_at=timezone.now() - datetime.timedelta(hours=2),
        )

        assert perform_all(AcceptMeetings()) == []

    def test_ignores_a_student_of_the_simulated_domain(self, teacher, course):
        advising = make_advising(make_demo_student(course), teacher)
        make_meeting(advising, author=advising.student.user)

        assert perform_all(AcceptMeetings()) == []


class TestStatesOutsideTheTable:
    def test_a_state_outside_the_table_generates_no_action(self, teacher, course):
        student = make_student(course=course)
        advising = make_advising(student, teacher)
        make_activity(advising)
        make_meeting(advising, author=teacher.user)
        make_user()

        for rule in (AcceptAdvisingRequests(), ReviewActivities(), AcceptMeetings()):
            assert list(rule.pending_actions()) == []


class TestFullCycle:
    def test_provisions_then_answers_everything_and_never_ends_an_advising(self, course, areas):
        requester = make_student(course=course, areas=areas[:1])
        assert not Teacher.objects.filter(user__email=SIMULATED_TEACHER_EMAIL).exists()

        call_command("run_personas", once=True)
        teacher = Teacher.objects.get(user__email=SIMULATED_TEACHER_EMAIL)
        advising_request = make_advising_request(requester, teacher)
        age_request(advising_request, 31)
        advised = make_advising(make_student(course=course), teacher)
        first = make_in_review_activity(advised)
        meeting = make_meeting(advised, author=advised.student.user)

        call_command("run_personas", once=True)

        advising_request.refresh_from_db()
        first.refresh_from_db()
        meeting.refresh_from_db()
        assert advising_request.status == AdvisingRequestStatus.ACCEPTED
        assert first.status == ActivityStatus.PENDING
        assert meeting.status == MeetingStatus.ACCEPTED
        assert not Advising.objects.filter(status=AdvisingStatus.ENDED).exists()
        assert User.objects.filter(is_active=False).count() == 0
