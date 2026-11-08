import datetime

import pytest
from django.utils import timezone

from apps.activities.models import (
    Activity,
    ActivityStatus,
    Comment,
    Review,
    ReviewDecision,
    Submission,
    SubmissionFile,
)
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db


def url(activity_id):
    return f"/api/atividades/{activity_id}"


def delete(client, user, activity_id):
    return client.delete(url(activity_id), headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=timezone.localdate() + datetime.timedelta(days=7),
        status=status,
        **extra,
    )


def test_the_active_advisor_deletes_logically_and_the_activity_disappears(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    kept = add_activity(advising)

    response = delete(client, teacher.user, activity.id)

    assert response.status_code == 204
    assert response.content == b""
    activity.refresh_from_db()
    assert activity.deleted_at is not None
    assert Activity.objects.filter(pk=activity.pk).exists()
    for user in (student.user, teacher.user):
        listed = client.get("/api/atividades", headers=bearer(user)).json()
        assert [item["id"] for item in listed["items"]] == [kept.id]
        assert client.get(url(activity.id), headers=bearer(user)).status_code == 404
    assert client.get(url(kept.id), headers=bearer(student.user)).status_code == 200


def test_the_submissions_files_reviews_and_comments_are_kept(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, status=ActivityStatus.IN_REVIEW)
    submission = Submission.objects.create(activity=activity)
    SubmissionFile.objects.create(
        submission=submission,
        file_name="a.pdf",
        size_bytes=10,
        content_type="application/pdf",
        storage_key="activities/1/1/0/a.pdf",
    )
    Review.objects.create(activity=activity, decision=ReviewDecision.REJECTED)
    Comment.objects.create(activity=activity, author=student.user, text="Feito.")

    assert delete(client, teacher.user, activity.id).status_code == 204

    assert Submission.objects.filter(activity=activity).count() == 1
    assert SubmissionFile.objects.filter(submission=submission).count() == 1
    assert Review.objects.filter(activity=activity).count() == 1
    assert Comment.objects.filter(activity=activity).count() == 1


@pytest.mark.parametrize(
    "status", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW]
)
def test_an_activity_can_be_deleted_while_pending_in_progress_or_in_review(client, status):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising, status=status)

    assert delete(client, teacher.user, activity.id).status_code == 204


def test_there_is_no_undo_and_deleting_twice_is_a_404(client):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    delete(client, teacher.user, activity.id)
    first_deleted_at = Activity.objects.get(pk=activity.pk).deleted_at

    again = delete(client, teacher.user, activity.id)

    assert again.status_code == 404
    assert Activity.objects.get(pk=activity.pk).deleted_at == first_deleted_at


def test_a_deleted_activity_leaves_the_counts_of_the_advisee_list(client):
    _, teacher, advising = advised_pair()
    gone = add_activity(advising)
    add_activity(advising)
    add_activity(advising, status=ActivityStatus.IN_PROGRESS)

    def counts():
        items = client.get("/api/orientacao/orientandos", headers=bearer(teacher.user)).json()
        return items["items"][0]["activities"]

    assert counts()["pending"] == 2
    delete(client, teacher.user, gone.id)

    assert counts() == {"pending": 1, "in_progress": 1, "in_review": 0, "completed": 0}
