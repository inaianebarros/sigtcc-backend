import datetime

import pytest
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import Student, Teacher, User
from apps.activities.models import (
    Activity,
    ActivityStatus,
    Review,
    ReviewDecision,
    StatusChange,
)
from apps.activities.services import review_activity
from apps.advising.expiration import expire_request
from apps.advising.models import (
    Advising,
    AdvisingRequest,
    AdvisingRequestStatus,
    AdvisingStatus,
    AnswerAction,
)
from apps.advising.services import answer_request, end_advising
from apps.dashboard.teacher import teacher_dashboard
from apps.meetings.models import MeetingStatus
from apps.personas.cycle import run_cycle
from apps.personas.simulated_students import (
    LINKED,
    LINKED_PLANS,
    MEETING_EXCUSES,
    PROFILES,
    REQUESTER,
    AcceptTeacherMeetings,
    DeliverActivities,
    RejectTeacherMeetings,
    RequestAdvisingAgain,
    SimulatedStudentProvisioning,
)
from apps.personas.simulated_teacher import SIMULATED_TEACHER_EMAIL
from config.errors import ConflictError
from tests.factories import make_advising, make_course, make_meeting, make_student, make_teacher

pytestmark = pytest.mark.django_db


@pytest.fixture
def real_teacher(course, areas):
    return make_teacher(courses=[course], areas=areas, full_name="Maria Souza")


def perform_all(rule):
    actions = list(rule.pending_actions())
    for action in actions:
        action.perform()
    return actions


def provision():
    return perform_all(SimulatedStudentProvisioning())


def student_of(profile, teacher) -> Student:
    return User.objects.get(email=profile.email_for(teacher)).student


def advising_of(profile, teacher) -> Advising:
    return Advising.objects.get(student=student_of(profile, teacher))


def activities_of(teacher):
    return Activity.objects.filter(advising=advising_of(LINKED, teacher)).order_by("deadline")


def requests_of_requester(teacher):
    return AdvisingRequest.objects.filter(student=student_of(REQUESTER, teacher)).order_by("id")


def pending_request_of_requester(teacher) -> AdvisingRequest:
    return requests_of_requester(teacher).get(status=AdvisingRequestStatus.PENDING)


def accept_requester(teacher) -> Advising:
    answer_request(teacher, pending_request_of_requester(teacher).pk, AnswerAction.ACCEPT)
    return advising_of(REQUESTER, teacher)


def reject_requester(teacher) -> None:
    answer_request(teacher, pending_request_of_requester(teacher).pk, AnswerAction.REJECT)


def age_activities(seconds):
    Activity.objects.update(created_at=timezone.now() - datetime.timedelta(seconds=seconds))


def age_reviews(seconds):
    Review.objects.update(decided_at=timezone.now() - datetime.timedelta(seconds=seconds))


def age_decisions(seconds):
    moment = timezone.now() - datetime.timedelta(seconds=seconds)
    AdvisingRequest.objects.exclude(status=AdvisingRequestStatus.PENDING).update(
        responded_at=moment
    )


def age_requests(delta):
    moment = timezone.now() - delta
    AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING).update(requested_at=moment)


def statuses(queryset):
    return [activity.status for activity in queryset]


class TestProvisioning:
    def test_a_new_real_teacher_gets_the_linked_and_the_requester_students(
        self, real_teacher, areas
    ):
        actions = provision()

        assert len(actions) == 2
        for profile in PROFILES:
            student = student_of(profile, real_teacher)
            assert student.user.email == profile.email_for(real_teacher)
            assert student.user.email.endswith(f"@{DEMO_DOMAIN}")
            assert "simulado" in student.user.full_name.lower()
            assert student.course == real_teacher.courses.get()
            assert set(student.areas_of_interest.all()) == set(areas)
        assert student_of(LINKED, real_teacher).registration_number == (
            f"SIM{real_teacher.user_id}-VNC"
        )
        assert student_of(REQUESTER, real_teacher).registration_number == (
            f"SIM{real_teacher.user_id}-SOL"
        )

    def test_the_linked_student_is_born_with_an_active_advising_a_topic_and_activities(
        self, real_teacher
    ):
        provision()

        advising = advising_of(LINKED, real_teacher)
        assert advising.status == AdvisingStatus.ACTIVE
        assert advising.teacher == real_teacher
        assert advising.thesis_topic.description
        assert advising.activities.count() == len(LINKED_PLANS) == 3

    def test_the_linked_student_has_two_activities_ahead_and_one_overdue(self, real_teacher):
        provision()
        today = timezone.localdate()

        deadlines = [activity.deadline for activity in activities_of(real_teacher)]

        assert [deadline < today for deadline in deadlines] == [True, False, False]
        assert set(statuses(activities_of(real_teacher))) == {ActivityStatus.PENDING}

    def test_the_requester_is_born_without_advising_and_with_a_pending_request(self, real_teacher):
        provision()
        student = student_of(REQUESTER, real_teacher)

        assert not Advising.objects.filter(student=student).exists()
        request = AdvisingRequest.objects.get(student=student)
        assert request.status == AdvisingRequestStatus.PENDING
        assert request.teacher == real_teacher
        assert not Activity.objects.filter(advising__student=student).exists()

    def test_the_student_names_carry_the_profile_label_and_the_teacher(self, real_teacher):
        provision()

        names = {student_of(profile, real_teacher).user.full_name for profile in PROFILES}
        assert names == {
            "Aluno Simulado Vinculado de Maria",
            "Aluno Simulado Solicitante de Maria",
        }

    def test_running_again_does_not_duplicate_anything(self, real_teacher):
        provision()
        counts = (
            User.objects.count(),
            Advising.objects.count(),
            Activity.objects.count(),
            AdvisingRequest.objects.count(),
        )

        assert provision() == []
        assert counts == (
            User.objects.count(),
            Advising.objects.count(),
            Activity.objects.count(),
            AdvisingRequest.objects.count(),
        )

    def test_every_real_teacher_gets_its_own_pair_without_limit(self, course, areas):
        teachers = [make_teacher(courses=[course], areas=areas) for _ in range(4)]

        assert len(provision()) == 8
        for teacher in teachers:
            assert Advising.objects.filter(teacher=teacher).count() == 1
            assert AdvisingRequest.objects.filter(teacher=teacher).count() == 2

    def test_an_answered_or_ended_state_never_makes_a_student_missing(self, real_teacher):
        provision()
        reject_requester(real_teacher)
        end_advising(
            real_teacher, advising_of(LINKED, real_teacher).pk, "Encerrada pelo professor."
        )
        users_before = User.objects.count()
        requests_before = AdvisingRequest.objects.count()

        assert provision() == []

        assert User.objects.count() == users_before
        assert AdvisingRequest.objects.count() == requests_before
        assert advising_of(LINKED, real_teacher).status == AdvisingStatus.ENDED

    def test_a_teacher_outside_computer_science_gets_no_students(self, course, areas):
        make_teacher(courses=[make_course(course.institute)], areas=areas)

        assert provision() == []

    def test_an_inactive_or_unavailable_teacher_gets_no_new_students(self, course, areas):
        make_teacher(courses=[course], areas=areas, is_active=False)
        unavailable = make_teacher(courses=[course], areas=areas, is_available=False)

        assert provision() == []

        Teacher.objects.filter(pk=unavailable.pk).update(is_available=True)
        assert len(provision()) == 2

    def test_demo_accounts_and_the_simulated_teacher_get_no_students(self, course, areas):
        demo = make_teacher(courses=[course], areas=areas)
        User.objects.filter(pk=demo.user_id).update(email=f"prof1.orientandos@{DEMO_DOMAIN}")
        simulated = make_teacher(courses=[course], areas=areas)
        User.objects.filter(pk=simulated.user_id).update(email=SIMULATED_TEACHER_EMAIL)

        assert provision() == []

    def test_does_nothing_without_the_course_catalog(self, db):
        assert provision() == []

    @pytest.mark.parametrize("profile", PROFILES, ids=lambda profile: profile.slug)
    def test_a_failure_in_the_middle_leaves_no_half_created_student(
        self, real_teacher, monkeypatch, profile
    ):
        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("apps.personas.simulated_students.request_advising", fail)
        action = next(
            action
            for action in SimulatedStudentProvisioning().pending_actions()
            if profile.slug in action.name
        )

        with pytest.raises(RuntimeError), transaction.atomic():
            action.perform()

        assert not User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").exists()


class TestLinkedStudentDelivery:
    def test_sends_the_activities_that_are_not_late_to_review_following_the_flow(
        self, real_teacher
    ):
        provision()
        age_activities(31)

        actions = perform_all(DeliverActivities())

        assert len(actions) == 2
        overdue, first, second = activities_of(real_teacher)
        for activity in (first, second):
            assert activity.status == ActivityStatus.IN_REVIEW
            assert activity.submissions.count() == 1
            assert activity.submissions.get().files.count() == 1
            assert [c.status for c in StatusChange.objects.filter(activity=activity)] == [
                ActivityStatus.IN_PROGRESS,
                ActivityStatus.IN_REVIEW,
            ]
        assert overdue.status == ActivityStatus.PENDING
        assert not overdue.submissions.exists()

    def test_never_delivers_the_overdue_activity(self, real_teacher):
        provision()
        age_activities(3600)

        perform_all(DeliverActivities())
        assert perform_all(DeliverActivities()) == []

        overdue = activities_of(real_teacher).first()
        assert overdue.status == ActivityStatus.PENDING
        assert not overdue.submissions.exists()

    def test_waits_for_the_delay_after_the_activity_is_created(self, real_teacher):
        provision()

        assert perform_all(DeliverActivities()) == []

    def test_does_the_flow_again_after_a_refusal(self, real_teacher):
        provision()
        age_activities(31)
        perform_all(DeliverActivities())
        activity = activities_of(real_teacher).filter(deadline__gt=timezone.localdate()).first()
        review_activity(real_teacher, activity.pk, ReviewDecision.REJECTED, "Ajustar o texto.")

        assert perform_all(DeliverActivities()) == []
        age_reviews(31)
        actions = perform_all(DeliverActivities())

        assert len(actions) == 1
        activity.refresh_from_db()
        assert activity.status == ActivityStatus.IN_REVIEW
        assert activity.submissions.count() == 2

    @pytest.mark.parametrize(
        "status", [ActivityStatus.IN_REVIEW, ActivityStatus.COMPLETED, ActivityStatus.ARCHIVED]
    )
    def test_ignores_an_activity_that_is_not_pending_or_in_progress(self, real_teacher, status):
        provision()
        age_activities(31)
        Activity.objects.update(status=status)

        assert perform_all(DeliverActivities()) == []

    def test_ignores_a_deleted_activity(self, real_teacher):
        provision()
        age_activities(31)
        Activity.objects.update(deleted_at=timezone.now())

        assert perform_all(DeliverActivities()) == []

    def test_does_not_deliver_for_the_requester_even_with_an_activity(self, real_teacher):
        provision()
        advising = accept_requester(real_teacher)
        Activity.objects.create(
            advising=advising,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        Activity.objects.filter(advising=advising).update(
            created_at=timezone.now() - datetime.timedelta(seconds=600)
        )

        assert perform_all(DeliverActivities()) == []

    def test_stops_after_the_real_teacher_ends_the_advising(self, real_teacher):
        provision()
        age_activities(600)
        end_advising(
            real_teacher, advising_of(LINKED, real_teacher).pk, "Encerrada pelo professor."
        )

        assert perform_all(DeliverActivities()) == []
        assert set(statuses(activities_of(real_teacher))) == {ActivityStatus.ARCHIVED}


class TestLinkedStudentMeetings:
    def test_accepts_a_pending_future_meeting_the_teacher_proposed_without_a_reason(
        self, real_teacher
    ):
        provision()
        meeting = make_meeting(advising_of(LINKED, real_teacher), author=real_teacher.user)

        actions = perform_all(AcceptTeacherMeetings())

        assert len(actions) == 1
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.ACCEPTED
        assert meeting.rejection_reason == ""

    @pytest.mark.parametrize(
        "status", [MeetingStatus.ACCEPTED, MeetingStatus.REJECTED, MeetingStatus.CANCELLED]
    )
    def test_ignores_a_meeting_that_is_no_longer_pending(self, real_teacher, status):
        provision()
        make_meeting(advising_of(LINKED, real_teacher), status=status, author=real_teacher.user)

        assert perform_all(AcceptTeacherMeetings()) == []

    def test_ignores_a_meeting_the_student_proposed_and_one_that_is_over(self, real_teacher):
        provision()
        advising = advising_of(LINKED, real_teacher)
        make_meeting(advising, author=advising.student.user)
        make_meeting(
            advising,
            author=real_teacher.user,
            scheduled_at=timezone.now() - datetime.timedelta(hours=2),
        )

        assert perform_all(AcceptTeacherMeetings()) == []

    def test_does_not_answer_the_meetings_of_the_requester(self, real_teacher):
        provision()
        advising = accept_requester(real_teacher)
        meeting = make_meeting(advising, author=real_teacher.user)

        assert perform_all(AcceptTeacherMeetings()) == []
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.PENDING


class TestRequesterMeetings:
    def test_rejects_a_pending_meeting_with_an_excuse_after_the_teacher_accepted(
        self, real_teacher
    ):
        provision()
        meeting = make_meeting(accept_requester(real_teacher), author=real_teacher.user)

        actions = perform_all(RejectTeacherMeetings())

        assert len(actions) == 1
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.REJECTED
        assert meeting.rejection_reason == MEETING_EXCUSES[meeting.id % len(MEETING_EXCUSES)]

    def test_the_same_meeting_always_gets_the_same_excuse(self, real_teacher):
        provision()
        advising = accept_requester(real_teacher)
        meetings = [make_meeting(advising, author=real_teacher.user) for _ in range(4)]

        perform_all(RejectTeacherMeetings())

        for meeting in meetings:
            meeting.refresh_from_db()
            assert meeting.rejection_reason == MEETING_EXCUSES[meeting.id % len(MEETING_EXCUSES)]

    def test_every_excuse_fits_the_meeting_reason_limit_and_is_filled(self):
        assert len(set(MEETING_EXCUSES)) == len(MEETING_EXCUSES) >= 3
        assert all(excuse.strip() and len(excuse) <= 500 for excuse in MEETING_EXCUSES)

    def test_does_not_act_before_the_teacher_accepts(self, real_teacher):
        provision()
        advising = advising_of(LINKED, real_teacher)
        make_meeting(advising, author=real_teacher.user)

        assert perform_all(RejectTeacherMeetings()) == []
        assert pending_request_of_requester(real_teacher).status == AdvisingRequestStatus.PENDING

    @pytest.mark.parametrize(
        "status", [MeetingStatus.ACCEPTED, MeetingStatus.REJECTED, MeetingStatus.CANCELLED]
    )
    def test_ignores_a_meeting_that_is_no_longer_pending(self, real_teacher, status):
        provision()
        make_meeting(accept_requester(real_teacher), status=status, author=real_teacher.user)

        assert perform_all(RejectTeacherMeetings()) == []

    def test_ignores_a_meeting_the_student_proposed_and_one_that_is_over(self, real_teacher):
        provision()
        advising = accept_requester(real_teacher)
        make_meeting(advising, author=advising.student.user)
        make_meeting(
            advising,
            author=real_teacher.user,
            scheduled_at=timezone.now() - datetime.timedelta(hours=2),
        )

        assert perform_all(RejectTeacherMeetings()) == []

    def test_does_not_answer_the_meetings_of_the_linked_student(self, real_teacher):
        provision()
        meeting = make_meeting(advising_of(LINKED, real_teacher), author=real_teacher.user)

        assert perform_all(RejectTeacherMeetings()) == []
        meeting.refresh_from_db()
        assert meeting.status == MeetingStatus.PENDING


class TestRequestAgain:
    def test_does_not_ask_while_the_request_is_pending(self, real_teacher):
        provision()
        age_decisions(3600)

        assert perform_all(RequestAdvisingAgain()) == []

    def test_asks_the_same_teacher_again_after_a_rejection_once_the_delay_has_passed(
        self, real_teacher
    ):
        provision()
        reject_requester(real_teacher)

        assert perform_all(RequestAdvisingAgain()) == []
        age_decisions(119)
        assert perform_all(RequestAdvisingAgain()) == []
        age_decisions(121)
        actions = perform_all(RequestAdvisingAgain())

        assert len(actions) == 1
        requests = list(requests_of_requester(real_teacher))
        assert [request.status for request in requests] == [
            AdvisingRequestStatus.REJECTED,
            AdvisingRequestStatus.PENDING,
        ]
        assert requests[1].teacher == real_teacher
        assert perform_all(RequestAdvisingAgain()) == []

    def test_asks_again_after_the_request_expired_once_the_delay_has_passed(self, real_teacher):
        provision()
        age_requests(datetime.timedelta(days=7, seconds=60))

        assert perform_all(RequestAdvisingAgain()) == []
        age_requests(datetime.timedelta(days=7, seconds=180))
        actions = perform_all(RequestAdvisingAgain())

        assert len(actions) == 1
        statuses_of_requests = [request.status for request in requests_of_requester(real_teacher)]
        assert statuses_of_requests == [
            AdvisingRequestStatus.UNANSWERED,
            AdvisingRequestStatus.PENDING,
        ]

    def test_asks_again_when_the_expired_request_was_already_marked_as_unanswered(
        self, real_teacher
    ):
        provision()
        age_requests(datetime.timedelta(days=7, seconds=180))
        assert expire_request(pending_request_of_requester(real_teacher).pk)

        assert len(perform_all(RequestAdvisingAgain())) == 1

    def test_the_delay_counts_from_the_latest_request_not_from_an_older_one(self, real_teacher):
        provision()
        reject_requester(real_teacher)
        age_decisions(3600)
        perform_all(RequestAdvisingAgain())
        reject_requester(real_teacher)

        assert perform_all(RequestAdvisingAgain()) == []

    def test_never_asks_again_after_the_teacher_ended_an_advising_with_it(self, real_teacher):
        provision()
        advising = accept_requester(real_teacher)
        end_advising(real_teacher, advising.pk, "Encerrada pelo professor.")
        age_decisions(3600)
        requests_before = AdvisingRequest.objects.count()

        assert perform_all(RequestAdvisingAgain()) == []
        assert provision() == []

        assert AdvisingRequest.objects.count() == requests_before
        advising.refresh_from_db()
        assert advising.status == AdvisingStatus.ENDED
        assert not Advising.objects.filter(
            student=advising.student, status=AdvisingStatus.ACTIVE
        ).exists()

    def test_never_asks_again_after_an_ended_advising_that_followed_a_rejection(self, real_teacher):
        provision()
        reject_requester(real_teacher)
        age_decisions(3600)
        perform_all(RequestAdvisingAgain())
        advising = accept_requester(real_teacher)
        end_advising(real_teacher, advising.pk, "Encerrada pelo professor.")
        age_decisions(3600)

        assert perform_all(RequestAdvisingAgain()) == []

    def test_does_not_ask_while_it_has_an_active_advising(self, real_teacher):
        provision()
        accept_requester(real_teacher)
        age_decisions(3600)

        assert perform_all(RequestAdvisingAgain()) == []

    def test_an_unavailable_teacher_makes_the_ask_a_skipped_action(self, real_teacher):
        provision()
        reject_requester(real_teacher)
        age_decisions(3600)
        Teacher.objects.filter(pk=real_teacher.pk).update(is_available=False)

        (action,) = list(RequestAdvisingAgain().pending_actions())
        with pytest.raises(ConflictError), transaction.atomic():
            action.perform()
        result = run_cycle([RequestAdvisingAgain()], max_actions=20)

        assert (result.actions, result.skipped, result.errors) == (0, 1, 0)
        assert requests_of_requester(real_teacher).count() == 1

    def test_a_deactivated_teacher_receives_no_new_request(self, real_teacher):
        provision()
        reject_requester(real_teacher)
        age_decisions(3600)
        User.objects.filter(pk=real_teacher.user_id).update(is_active=False)

        assert perform_all(RequestAdvisingAgain()) == []

    def test_never_asks_for_the_linked_student(self, real_teacher):
        provision()
        end_advising(
            real_teacher, advising_of(LINKED, real_teacher).pk, "Encerrada pelo professor."
        )
        age_decisions(3600)

        assert perform_all(RequestAdvisingAgain()) == []


class TestAttentionList:
    def test_the_linked_student_with_an_overdue_activity_is_listed_and_counted(self, real_teacher):
        provision()

        dashboard = teacher_dashboard(real_teacher)

        assert dashboard["advisees_count"] == 1
        attention = dashboard["attention_advisees"]["items"]
        linked = student_of(LINKED, real_teacher)
        assert [item["id"] for item in attention] == [linked.user_id]
        assert attention[0]["overdue_count"] == 1
        assert dashboard["activities"]["overdue"] == 1
        assert dashboard["activities"]["total"] == len(LINKED_PLANS)
        assert dashboard["activities"]["progress_percent"] == 0

    def test_it_stays_listed_after_the_deliveries(self, real_teacher):
        provision()
        age_activities(600)
        perform_all(DeliverActivities())

        dashboard = teacher_dashboard(real_teacher)

        attention = dashboard["attention_advisees"]["items"]
        assert [item["overdue_count"] for item in attention] == [1]
        assert dashboard["activities"]["overdue"] == 1

    def test_the_requester_without_advising_is_not_an_advisee(self, real_teacher):
        provision()

        assert teacher_dashboard(real_teacher)["advisees_count"] == 1
        accept_requester(real_teacher)
        assert teacher_dashboard(real_teacher)["advisees_count"] == 2


class TestBarriers:
    def test_ignores_an_advising_with_a_teacher_other_than_the_owner(self, real_teacher, course):
        provision()
        other_teacher = make_teacher(courses=[course])
        end_advising(
            real_teacher, advising_of(LINKED, real_teacher).pk, "Encerrada pelo professor."
        )
        simulated = student_of(LINKED, real_teacher)
        foreign = make_advising(simulated, other_teacher)
        Activity.objects.create(
            advising=foreign,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        make_meeting(foreign, author=other_teacher.user)
        age_activities(600)

        assert perform_all(DeliverActivities()) == []
        assert perform_all(AcceptTeacherMeetings()) == []

    def test_the_requester_does_not_act_with_a_teacher_other_than_the_owner(
        self, real_teacher, course
    ):
        provision()
        other_teacher = make_teacher(courses=[course])
        requester = student_of(REQUESTER, real_teacher)
        AdvisingRequest.objects.filter(student=requester).delete()
        foreign_request = AdvisingRequest.objects.create(
            student=requester,
            teacher=other_teacher,
            status=AdvisingRequestStatus.REJECTED,
            responded_at=timezone.now() - datetime.timedelta(hours=1),
        )
        assert foreign_request.teacher == other_teacher

        assert perform_all(RequestAdvisingAgain()) == []

    def test_never_interacts_with_the_simulated_teacher(self, course):
        simulated_teacher = make_teacher(courses=[course])
        User.objects.filter(pk=simulated_teacher.user_id).update(email=SIMULATED_TEACHER_EMAIL)
        simulated_teacher.refresh_from_db()
        student = make_student(course=course)
        User.objects.filter(pk=student.user_id).update(email=LINKED.email_for(simulated_teacher))
        advising = make_advising(student, simulated_teacher)
        Activity.objects.create(
            advising=advising,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        make_meeting(advising, author=simulated_teacher.user)
        requester = make_student(course=course)
        User.objects.filter(pk=requester.user_id).update(
            email=REQUESTER.email_for(simulated_teacher)
        )
        AdvisingRequest.objects.create(
            student=requester,
            teacher=simulated_teacher,
            status=AdvisingRequestStatus.REJECTED,
            responded_at=timezone.now() - datetime.timedelta(hours=1),
        )
        age_activities(600)

        for rule in (
            DeliverActivities(),
            AcceptTeacherMeetings(),
            RejectTeacherMeetings(),
            RequestAdvisingAgain(),
        ):
            assert perform_all(rule) == []

    def test_ignores_a_real_student_with_the_real_teacher(self, real_teacher, course):
        advising = make_advising(make_student(course=course), real_teacher)
        Activity.objects.create(
            advising=advising,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        make_meeting(advising, author=real_teacher.user)
        rejected = make_student(course=course)
        AdvisingRequest.objects.create(
            student=rejected,
            teacher=real_teacher,
            status=AdvisingRequestStatus.REJECTED,
            responded_at=timezone.now() - datetime.timedelta(hours=1),
        )
        age_activities(600)

        for rule in (
            DeliverActivities(),
            AcceptTeacherMeetings(),
            RejectTeacherMeetings(),
            RequestAdvisingAgain(),
        ):
            assert perform_all(rule) == []

    def test_the_rules_never_end_an_advising_or_deactivate_an_account(self, real_teacher):
        provision()
        age_activities(600)
        accept_requester(real_teacher)
        make_meeting(advising_of(REQUESTER, real_teacher), author=real_teacher.user)
        make_meeting(advising_of(LINKED, real_teacher), author=real_teacher.user)
        for rule in (
            DeliverActivities(),
            AcceptTeacherMeetings(),
            RejectTeacherMeetings(),
            RequestAdvisingAgain(),
        ):
            perform_all(rule)

        assert not Advising.objects.filter(status=AdvisingStatus.ENDED).exists()
        assert not User.objects.filter(is_active=False).exists()


class TestFullCycle:
    def test_a_cycle_provisions_the_pair_and_the_next_ones_react_without_duplicating(
        self, real_teacher
    ):
        call_command("run_personas", once=True)

        assert Advising.objects.filter(teacher=real_teacher).count() == 1
        assert pending_request_of_requester(real_teacher).teacher == real_teacher
        assert User.objects.filter(email=SIMULATED_TEACHER_EMAIL).exists()
        assert not Review.objects.exists()

        age_activities(600)
        call_command("run_personas", once=True)

        assert statuses(activities_of(real_teacher)) == [
            ActivityStatus.PENDING,
            ActivityStatus.IN_REVIEW,
            ActivityStatus.IN_REVIEW,
        ]
        assert Review.objects.count() == 0
        assert pending_request_of_requester(real_teacher)

        users, advisings = User.objects.count(), Advising.objects.count()
        call_command("run_personas", once=True)

        assert (User.objects.count(), Advising.objects.count()) == (users, advisings)

    def test_the_meetings_are_answered_by_each_profile_and_a_rejection_leads_to_a_new_request(
        self, real_teacher
    ):
        call_command("run_personas", once=True)
        linked_meeting = make_meeting(advising_of(LINKED, real_teacher), author=real_teacher.user)
        requester_advising = accept_requester(real_teacher)
        requester_meeting = make_meeting(requester_advising, author=real_teacher.user)

        call_command("run_personas", once=True)

        linked_meeting.refresh_from_db()
        requester_meeting.refresh_from_db()
        assert linked_meeting.status == MeetingStatus.ACCEPTED
        assert requester_meeting.status == MeetingStatus.REJECTED
        assert requester_meeting.rejection_reason in MEETING_EXCUSES

    def test_a_rejected_request_is_asked_again_by_a_later_cycle(self, real_teacher):
        call_command("run_personas", once=True)
        reject_requester(real_teacher)

        call_command("run_personas", once=True)
        assert requests_of_requester(real_teacher).count() == 1

        age_decisions(3600)
        call_command("run_personas", once=True)

        assert requests_of_requester(real_teacher).count() == 2
        assert pending_request_of_requester(real_teacher).teacher == real_teacher

    def test_the_cycle_limit_caps_the_provisioning(self, settings, real_teacher):
        settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 2

        call_command("run_personas", once=True)

        assert User.objects.filter(email=SIMULATED_TEACHER_EMAIL).exists()
        assert Advising.objects.filter(teacher=real_teacher).count() == 1
        assert not User.objects.filter(email=REQUESTER.email_for(real_teacher)).exists()
