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
from apps.advising.models import Advising, AdvisingRequest, AdvisingStatus
from apps.advising.services import end_advising
from apps.dashboard.teacher import teacher_dashboard
from apps.personas.simulated_students import (
    ACTIVE,
    ACTIVE_DELIVERY_DELAY,
    BALANCED,
    BALANCED_DELIVERY_DELAY,
    LATE,
    PROFILES,
    DeliverActivities,
    SimulatedStudentProvisioning,
)
from apps.personas.simulated_teacher import SIMULATED_TEACHER_EMAIL
from tests.factories import make_advising, make_course, make_student, make_teacher

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


def active_delivery():
    return DeliverActivities("test_active", ACTIVE, ACTIVE_DELIVERY_DELAY)


def balanced_delivery():
    return DeliverActivities("test_balanced", BALANCED, BALANCED_DELIVERY_DELAY, one_at_a_time=True)


def student_of(profile, teacher) -> Student:
    return User.objects.get(email=profile.email_for(teacher)).student


def advising_of(profile, teacher) -> Advising:
    return Advising.objects.get(student=student_of(profile, teacher))


def activities_of(profile, teacher):
    return Activity.objects.filter(advising=advising_of(profile, teacher)).order_by("deadline")


def age_activities(seconds):
    Activity.objects.update(created_at=timezone.now() - datetime.timedelta(seconds=seconds))


def age_reviews(seconds):
    Review.objects.update(decided_at=timezone.now() - datetime.timedelta(seconds=seconds))


def statuses(queryset):
    return [activity.status for activity in queryset]


class TestProvisioning:
    def test_a_new_real_teacher_gets_the_three_simulated_students_with_an_active_advising(
        self, real_teacher, areas
    ):
        actions = provision()

        assert len(actions) == 3
        for profile in PROFILES:
            student = student_of(profile, real_teacher)
            advising = advising_of(profile, real_teacher)
            assert advising.status == AdvisingStatus.ACTIVE
            assert advising.teacher == real_teacher
            assert advising.thesis_topic.description == profile.topic
            assert student.user.email.endswith(f"@{DEMO_DOMAIN}")
            assert "simulado" in student.user.full_name.lower()
            assert student.course == real_teacher.courses.get()
            assert set(student.areas_of_interest.all()) == set(areas)
            assert advising.activities.count() == len(profile.plans)

    def test_the_student_names_carry_the_profile_label_and_the_teacher(self, real_teacher):
        provision()

        names = {student_of(profile, real_teacher).user.full_name for profile in PROFILES}
        assert names == {
            "Aluno Simulado Ativo de Maria",
            "Aluno Simulado em Atraso de Maria",
            "Aluno Simulado Equilibrado de Maria",
        }

    def test_the_deadlines_follow_the_profile(self, real_teacher):
        provision()
        today = timezone.localdate()

        assert all(a.deadline < today for a in activities_of(LATE, real_teacher))
        for profile in (ACTIVE, BALANCED):
            assert all(a.deadline > today for a in activities_of(profile, real_teacher))

    def test_running_again_does_not_duplicate_anything(self, real_teacher):
        provision()
        counts = (User.objects.count(), Advising.objects.count(), Activity.objects.count())

        assert provision() == []
        assert counts == (User.objects.count(), Advising.objects.count(), Activity.objects.count())

    def test_every_real_teacher_gets_its_own_trio_without_limit(self, course, areas):
        teachers = [make_teacher(courses=[course], areas=areas) for _ in range(4)]

        assert len(provision()) == 12
        for teacher in teachers:
            assert Advising.objects.filter(teacher=teacher).count() == 3

    def test_an_ended_advising_is_neither_reopened_nor_recreated(self, real_teacher):
        provision()
        advising = advising_of(ACTIVE, real_teacher)
        end_advising(real_teacher, advising.pk, "Encerrada pelo professor real.")
        users_before = User.objects.count()
        requests_before = AdvisingRequest.objects.count()

        assert provision() == []

        advising.refresh_from_db()
        assert advising.status == AdvisingStatus.ENDED
        assert User.objects.count() == users_before
        assert AdvisingRequest.objects.count() == requests_before
        assert Advising.objects.filter(status=AdvisingStatus.ACTIVE).count() == 2

    def test_the_behavior_rules_stop_after_the_real_teacher_ends_the_advising(self, real_teacher):
        provision()
        age_activities(600)
        end_advising(
            real_teacher, advising_of(ACTIVE, real_teacher).pk, "Encerrada pelo professor."
        )
        end_advising(
            real_teacher, advising_of(BALANCED, real_teacher).pk, "Encerrada pelo professor."
        )

        assert perform_all(active_delivery()) == []
        assert perform_all(balanced_delivery()) == []
        archived = activities_of(ACTIVE, real_teacher)
        assert set(statuses(archived)) == {ActivityStatus.ARCHIVED}

    def test_a_teacher_outside_computer_science_gets_no_trio(self, course, areas):
        make_teacher(courses=[make_course(course.institute)], areas=areas)

        assert provision() == []

    def test_an_inactive_or_unavailable_teacher_gets_no_new_trio(self, course, areas):
        make_teacher(courses=[course], areas=areas, is_active=False)
        unavailable = make_teacher(courses=[course], areas=areas, is_available=False)

        assert provision() == []

        Teacher.objects.filter(pk=unavailable.pk).update(is_available=True)
        assert len(provision()) == 3

    def test_demo_accounts_and_the_simulated_teacher_get_no_trio(self, course, areas):
        demo = make_teacher(courses=[course], areas=areas)
        User.objects.filter(pk=demo.user_id).update(email=f"prof1.orientandos@{DEMO_DOMAIN}")
        simulated = make_teacher(courses=[course], areas=areas)
        User.objects.filter(pk=simulated.user_id).update(email=SIMULATED_TEACHER_EMAIL)

        assert provision() == []

    def test_does_nothing_without_the_course_catalog(self, db):
        assert provision() == []

    def test_a_failure_in_the_middle_leaves_no_half_created_student(
        self, real_teacher, monkeypatch
    ):
        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("apps.personas.simulated_students.create_activity", fail)

        action = next(iter(SimulatedStudentProvisioning().pending_actions()))

        with pytest.raises(RuntimeError), transaction.atomic():
            action.perform()

        assert not User.objects.filter(email__endswith=f"@{DEMO_DOMAIN}").exists()


class TestActiveStudent:
    def test_sends_every_activity_to_review_following_the_flow(self, real_teacher):
        provision()
        age_activities(31)

        actions = perform_all(active_delivery())

        assert len(actions) == 3
        for activity in activities_of(ACTIVE, real_teacher):
            assert activity.status == ActivityStatus.IN_REVIEW
            assert activity.submissions.count() == 1
            assert activity.submissions.get().files.count() == 1
            assert [c.status for c in StatusChange.objects.filter(activity=activity)] == [
                ActivityStatus.IN_PROGRESS,
                ActivityStatus.IN_REVIEW,
            ]

    def test_waits_for_the_delay_after_the_activity_is_created(self, real_teacher):
        provision()

        assert perform_all(active_delivery()) == []

    def test_does_the_flow_again_after_a_refusal(self, real_teacher):
        provision()
        age_activities(31)
        perform_all(active_delivery())
        activity = activities_of(ACTIVE, real_teacher).first()
        review_activity(real_teacher, activity.pk, ReviewDecision.REJECTED, "Ajustar o texto.")

        assert perform_all(active_delivery()) == []
        age_reviews(31)
        actions = perform_all(active_delivery())

        assert len(actions) == 1
        activity.refresh_from_db()
        assert activity.status == ActivityStatus.IN_REVIEW
        assert activity.submissions.count() == 2

    def test_leaves_alone_a_late_activity(self, real_teacher):
        provision()
        age_activities(31)
        Activity.objects.filter(advising=advising_of(ACTIVE, real_teacher)).update(
            deadline=timezone.localdate() - datetime.timedelta(days=1)
        )

        assert perform_all(active_delivery()) == []

    @pytest.mark.parametrize(
        "status", [ActivityStatus.IN_REVIEW, ActivityStatus.COMPLETED, ActivityStatus.ARCHIVED]
    )
    def test_ignores_an_activity_that_is_not_pending_or_in_progress(self, real_teacher, status):
        provision()
        age_activities(31)
        Activity.objects.filter(advising=advising_of(ACTIVE, real_teacher)).update(status=status)

        assert perform_all(active_delivery()) == []

    def test_ignores_a_deleted_activity(self, real_teacher):
        provision()
        age_activities(31)
        Activity.objects.update(deleted_at=timezone.now())

        assert perform_all(active_delivery()) == []

    def test_ignores_the_students_of_the_other_profiles(self, real_teacher):
        provision()
        age_activities(31)

        perform_all(active_delivery())

        assert set(statuses(activities_of(LATE, real_teacher))) == {ActivityStatus.PENDING}
        assert set(statuses(activities_of(BALANCED, real_teacher))) == {ActivityStatus.PENDING}


class TestLateStudent:
    def test_never_delivers_and_keeps_its_deadlines_overdue(self, real_teacher):
        provision()
        age_activities(3600)

        for rule in (active_delivery(), balanced_delivery()):
            perform_all(rule)

        for activity in activities_of(LATE, real_teacher):
            assert activity.status == ActivityStatus.PENDING
            assert not activity.submissions.exists()

    def test_appears_in_the_attention_list_and_the_dashboard_shows_the_progress(self, real_teacher):
        provision()

        dashboard = teacher_dashboard(real_teacher)

        assert dashboard["advisees_count"] == 3
        attention = {item["id"]: item for item in dashboard["attention_advisees"]["items"]}
        late_student = student_of(LATE, real_teacher)
        assert attention[late_student.user_id]["overdue_count"] == len(LATE.plans)
        assert dashboard["activities"]["overdue"] == len(LATE.plans)
        assert dashboard["activities"]["total"] == sum(len(p.plans) for p in PROFILES)
        assert dashboard["activities"]["progress_percent"] == 0

    def test_the_other_profiles_are_not_late_before_delivering(self, real_teacher):
        provision()

        attention = teacher_dashboard(real_teacher)["attention_advisees"]["items"]

        assert [item["id"] for item in attention] == [student_of(LATE, real_teacher).user_id]


class TestBalancedStudent:
    def test_delivers_one_activity_at_a_time_before_its_deadline(self, real_teacher):
        provision()
        age_activities(121)

        actions = perform_all(balanced_delivery())

        assert len(actions) == 1
        first, second, third = activities_of(BALANCED, real_teacher)
        assert (first.status, second.status, third.status) == (
            ActivityStatus.IN_REVIEW,
            ActivityStatus.PENDING,
            ActivityStatus.PENDING,
        )
        assert first.submissions.get().submitted_at.date() <= first.deadline
        assert perform_all(balanced_delivery()) == []

    def test_sends_the_next_one_after_the_teacher_reviews_the_previous(self, real_teacher):
        provision()
        age_activities(121)
        perform_all(balanced_delivery())
        first = activities_of(BALANCED, real_teacher).first()
        review_activity(real_teacher, first.pk, ReviewDecision.ACCEPTED)

        actions = perform_all(balanced_delivery())

        assert len(actions) == 1
        assert statuses(activities_of(BALANCED, real_teacher)) == [
            ActivityStatus.COMPLETED,
            ActivityStatus.IN_REVIEW,
            ActivityStatus.PENDING,
        ]

    def test_waits_longer_than_the_active_student(self, real_teacher):
        provision()
        age_activities(31)

        assert perform_all(balanced_delivery()) == []

    def test_never_delivers_a_late_activity(self, real_teacher):
        provision()
        age_activities(121)
        Activity.objects.filter(advising=advising_of(BALANCED, real_teacher)).update(
            deadline=timezone.localdate() - datetime.timedelta(days=1)
        )

        assert perform_all(balanced_delivery()) == []


class TestBarriers:
    def test_ignores_an_advising_with_a_teacher_other_than_the_owner(self, real_teacher, course):
        provision()
        other_teacher = make_teacher(courses=[course])
        advising = advising_of(ACTIVE, real_teacher)
        end_advising(real_teacher, advising.pk, "Encerrada pelo professor.")
        simulated = student_of(ACTIVE, real_teacher)
        foreign = make_advising(simulated, other_teacher)
        Activity.objects.create(
            advising=foreign,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        age_activities(600)

        assert perform_all(active_delivery()) == []

    def test_never_interacts_with_the_simulated_teacher(self, course):
        simulated_teacher = make_teacher(courses=[course])
        User.objects.filter(pk=simulated_teacher.user_id).update(email=SIMULATED_TEACHER_EMAIL)
        simulated_teacher.refresh_from_db()
        student = make_student(course=course)
        User.objects.filter(pk=student.user_id).update(email=ACTIVE.email_for(simulated_teacher))
        advising = make_advising(student, simulated_teacher)
        Activity.objects.create(
            advising=advising,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        age_activities(600)

        assert perform_all(active_delivery()) == []
        assert perform_all(balanced_delivery()) == []

    def test_ignores_a_real_student_with_the_real_teacher(self, real_teacher, course):
        advising = make_advising(make_student(course=course), real_teacher)
        Activity.objects.create(
            advising=advising,
            title="Atividade",
            description="Descrição",
            deadline=timezone.localdate() + datetime.timedelta(days=5),
        )
        age_activities(600)

        assert perform_all(active_delivery()) == []
        assert perform_all(balanced_delivery()) == []

    def test_the_profiles_never_end_an_advising_or_deactivate_an_account(self, real_teacher):
        provision()
        age_activities(600)
        perform_all(active_delivery())
        perform_all(balanced_delivery())

        assert not Advising.objects.filter(status=AdvisingStatus.ENDED).exists()
        assert not User.objects.filter(is_active=False).exists()


class TestFullCycle:
    def test_a_cycle_provisions_the_trio_and_the_next_ones_deliver_without_duplicating(
        self, real_teacher
    ):
        call_command("run_personas", once=True)

        assert Advising.objects.filter(teacher=real_teacher).count() == 3
        assert User.objects.filter(email=SIMULATED_TEACHER_EMAIL).exists()
        assert not Review.objects.exists()

        age_activities(600)
        call_command("run_personas", once=True)

        assert set(statuses(activities_of(ACTIVE, real_teacher))) == {ActivityStatus.IN_REVIEW}
        assert statuses(activities_of(BALANCED, real_teacher)).count(ActivityStatus.IN_REVIEW) == 1
        assert set(statuses(activities_of(LATE, real_teacher))) == {ActivityStatus.PENDING}
        assert Review.objects.count() == 0

        users, advisings = User.objects.count(), Advising.objects.count()
        call_command("run_personas", once=True)

        assert (User.objects.count(), Advising.objects.count()) == (users, advisings)

    def test_the_cycle_limit_caps_the_provisioning(self, settings, real_teacher):
        settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 2

        call_command("run_personas", once=True)

        assert Advising.objects.filter(teacher=real_teacher).count() == 1
