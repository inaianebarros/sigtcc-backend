"""The active advisings and their activities in the demonstration data (#140)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from ninja.errors import HttpError

from apps.accounts.demo_activities import (
    COMPLETED_TITLE,
    IN_PROGRESS_TITLE,
    IN_REVIEW_TITLE,
    OVERDUE_TITLE,
    PENDING_TITLE,
    REFUSED_TITLE,
)
from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import User
from apps.activities.models import (
    Activity,
    ActivityStatus,
    Comment,
    Review,
    ReviewDecision,
    Submission,
    SubmissionFile,
)
from apps.advising.models import Advising, AdvisingStatus
from apps.notifications.models import Notification, NotificationType
from tests.api.helpers import bearer

pytestmark = pytest.mark.django_db

LIST_URL = "/api/atividades"
COMPLETE_STUDENT = "aluno5.orientacao-completa"
EMPTY_STUDENT = "aluno6.orientacao-vazia"
TEACHER = "prof1.orientandos"


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def seed():
    out = StringIO()
    call_command("seed_demo_data", stdout=out)
    return out.getvalue()


@pytest.fixture
def seeded(catalog, debug):
    seed()


def activity(title):
    return Activity.objects.get(advising__student__user=user_of(COMPLETE_STUDENT), title=title)


def complete_student_activities():
    return Activity.objects.filter(advising__student__user=user_of(COMPLETE_STUDENT))


def listed(client, slug, **params):
    response = client.get(LIST_URL, params, headers=bearer(user_of(slug)))
    return {item["title"]: item for item in response.json()["items"]}


def test_students_five_and_six_have_an_active_advising_with_the_first_teacher(seeded):
    teacher = user_of(TEACHER).teacher

    for slug in (COMPLETE_STUDENT, EMPTY_STUDENT):
        advising = Advising.objects.get(student__user=user_of(slug))
        assert advising.status == AdvisingStatus.ACTIVE
        assert advising.teacher == teacher
        assert timezone.now() - advising.started_at > timedelta(days=7)


def test_the_complete_student_has_activities_in_every_open_situation(client, seeded):
    items = listed(client, COMPLETE_STUDENT)

    assert {title: item["status"] for title, item in items.items()} == {
        PENDING_TITLE: "pending",
        IN_PROGRESS_TITLE: "in_progress",
        IN_REVIEW_TITLE: "in_review",
        COMPLETED_TITLE: "completed",
        OVERDUE_TITLE: "pending",
        REFUSED_TITLE: "pending",
    }


def test_exactly_one_activity_is_overdue_and_the_server_says_so(client, seeded):
    overdue = listed(client, COMPLETE_STUDENT, overdue="true")

    assert list(overdue) == [OVERDUE_TITLE]
    assert Activity.objects.get(title=OVERDUE_TITLE).deadline < timezone.localdate()


def test_the_deadlines_of_the_other_activities_are_in_the_future(seeded):
    open_titles = [PENDING_TITLE, IN_PROGRESS_TITLE, IN_REVIEW_TITLE, REFUSED_TITLE]

    for title in open_titles:
        assert activity(title).deadline >= timezone.localdate()


def test_no_activity_of_the_active_advising_is_archived_or_deleted(seeded):
    advising = Advising.objects.get(student__user=user_of(COMPLETE_STUDENT))

    assert not Activity.objects.filter(advising=advising, status=ActivityStatus.ARCHIVED).exists()
    assert not Activity.objects.filter(advising=advising, deleted_at__isnull=False).exists()


def test_the_in_review_and_completed_activities_have_deliveries_with_files(seeded):
    for title in (IN_REVIEW_TITLE, COMPLETED_TITLE, REFUSED_TITLE):
        submissions = Submission.objects.filter(activity=activity(title))
        assert submissions.exists()
        assert all(submission.files.count() >= 1 for submission in submissions)
    assert SubmissionFile.objects.filter(size_bytes__gt=0).count() >= 3
    assert not Submission.objects.filter(activity=activity(PENDING_TITLE)).exists()


def test_the_completed_activity_was_accepted_and_the_refused_one_has_its_justification(seeded):
    accepted = Review.objects.get(activity=activity(COMPLETED_TITLE))
    refused = Review.objects.get(activity=activity(REFUSED_TITLE))

    assert accepted.decision == ReviewDecision.ACCEPTED
    assert refused.decision == ReviewDecision.REJECTED
    assert refused.justification
    assert not Review.objects.filter(activity=activity(IN_REVIEW_TITLE)).exists()


def test_the_teacher_has_an_activity_waiting_for_the_review(client, seeded):
    items = listed(client, TEACHER, status="in_review")

    assert list(items) == [IN_REVIEW_TITLE]


def test_the_student_and_the_teacher_comment_on_the_activities(seeded):
    comments = Comment.objects.filter(activity__advising__student__user=user_of(COMPLETE_STUDENT))

    authors = set(comments.values_list("author__email", flat=True))
    assert authors == {
        f"{COMPLETE_STUDENT}@{DEMO_DOMAIN}",
        f"{TEACHER}@{DEMO_DOMAIN}",
    }
    assert Comment.objects.filter(activity=activity(IN_PROGRESS_TITLE)).count() >= 2


def test_the_services_notify_both_sides(seeded):
    teacher, student = user_of(TEACHER), user_of(COMPLETE_STUDENT)

    def count(recipient, kind):
        return Notification.objects.filter(recipient=recipient, type=kind).count()

    assert count(student, NotificationType.ACTIVITY_CREATED) == 6
    assert count(student, NotificationType.ACTIVITY_REVIEW_RESULT) == 2
    assert count(teacher, NotificationType.ACTIVITY_AWAITING_REVIEW) >= 1
    assert count(teacher, NotificationType.ACTIVITY_SUBMITTED) >= 1
    assert count(student, NotificationType.ACTIVITY_COMMENTED) >= 1
    assert count(teacher, NotificationType.ACTIVITY_COMMENTED) >= 1


def test_the_sixth_student_has_an_active_advising_without_topic_or_activities(client, seeded):
    advising = Advising.objects.get(student__user=user_of(EMPTY_STUDENT))

    assert not Activity.objects.filter(advising=advising).exists()
    assert not hasattr(advising, "thesis_topic")
    assert listed(client, EMPTY_STUDENT) == {}


def test_the_teacher_lists_the_two_advisees_with_their_counts(client, seeded):
    response = client.get("/api/orientacao/orientandos", headers=bearer(user_of(TEACHER)))

    items = {item["student"]["full_name"]: item for item in response.json()["items"]}
    assert set(items) == {user_of(COMPLETE_STUDENT).full_name, user_of(EMPTY_STUDENT).full_name}
    assert items[user_of(COMPLETE_STUDENT).full_name]["activities"] == {
        "pending": 3,
        "in_progress": 1,
        "in_review": 1,
        "completed": 1,
    }


def test_running_it_again_duplicates_nothing(seeded):
    counts = (
        Advising.objects.count(),
        Activity.objects.count(),
        Submission.objects.count(),
        Comment.objects.count(),
        Review.objects.count(),
        Notification.objects.count(),
    )

    seed()

    assert counts == (
        Advising.objects.count(),
        Activity.objects.count(),
        Submission.objects.count(),
        Comment.objects.count(),
        Review.objects.count(),
        Notification.objects.count(),
    )


def test_without_the_storage_only_the_activities_that_need_files_are_skipped(catalog, debug):
    broken = MagicMock()
    broken.save.side_effect = ConnectionError("armazenamento fora do ar")

    with patch("apps.activities.services.submission_storage", return_value=broken):
        output = seed()

    titles = set(complete_student_activities().values_list("title", flat=True))
    assert titles == {PENDING_TITLE, OVERDUE_TITLE, IN_PROGRESS_TITLE}
    assert "armazenamento" in output.lower()
    assert Submission.objects.count() == 0


def test_running_again_with_the_storage_back_completes_the_skipped_activities(catalog, debug):
    broken = MagicMock()
    broken.save.side_effect = ConnectionError("armazenamento fora do ar")
    with patch("apps.activities.services.submission_storage", return_value=broken):
        seed()

    seed()

    titles = set(complete_student_activities().values_list("title", flat=True))
    assert titles == {
        PENDING_TITLE,
        IN_PROGRESS_TITLE,
        IN_REVIEW_TITLE,
        COMPLETED_TITLE,
        OVERDUE_TITLE,
        REFUSED_TITLE,
    }
    assert complete_student_activities().count() == 6
    assert Review.objects.count() == 2


def test_the_in_progress_activity_has_comments_of_both_sides(seeded):
    authors = set(
        Comment.objects.filter(activity=activity(IN_PROGRESS_TITLE)).values_list(
            "author__email", flat=True
        )
    )

    assert authors == {f"{COMPLETE_STUDENT}@{DEMO_DOMAIN}", f"{TEACHER}@{DEMO_DOMAIN}"}


def test_an_error_that_is_not_the_storage_stops_the_command(catalog, debug):
    with (
        patch(
            "apps.accounts.demo_activities.create_activity",
            side_effect=HttpError(404, "Aluno não encontrado."),
        ),
        pytest.raises(HttpError),
    ):
        seed()

    assert Activity.objects.count() == 0


def test_the_complete_student_downloads_a_delivered_file(client, seeded):
    delivered = SubmissionFile.objects.filter(
        submission__activity=activity(IN_REVIEW_TITLE)
    ).first()

    response = client.get(
        f"{LIST_URL}/{delivered.submission.activity_id}/arquivos/{delivered.id}",
        headers=bearer(user_of(COMPLETE_STUDENT)),
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF-")


def test_the_dashboard_shows_the_counts_the_progress_and_the_next_activities(client, seeded):
    response = client.get("/api/painel/aluno", headers=bearer(user_of(COMPLETE_STUDENT)))

    body = response.json()
    assert body["has_active_advising"] is True
    assert body["activities"]["total"] == 6
    assert body["activities"]["overdue"] == 1
    assert body["activities"]["progress_percent"] > 0
    assert len(body["next_activities"]["items"]) >= 1
