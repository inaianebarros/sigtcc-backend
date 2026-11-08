import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
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
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db


def detail_url(activity_id):
    return f"/api/atividades/{activity_id}"


def get_detail(client, user, activity_id):
    return client.get(detail_url(activity_id), headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING, deadline=None, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline=deadline or timezone.localdate() + datetime.timedelta(days=5),
        status=status,
        **extra,
    )


def add_submission(activity, files=(("capitulo2.pdf", 1024, "application/pdf"),)):
    submission = Submission.objects.create(activity=activity)
    for index, (name, size, content_type) in enumerate(files):
        SubmissionFile.objects.create(
            submission=submission,
            file_name=name,
            size_bytes=size,
            content_type=content_type,
            storage_key=f"activities/{activity.id}/{submission.id}/{index}/{name}",
        )
    return submission


def test_the_student_and_the_teacher_open_the_detail_of_an_activity(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising, status=ActivityStatus.IN_REVIEW)
    submission = add_submission(
        activity, [("a.pdf", 10, "application/pdf"), ("b.zip", 20, "application/zip")]
    )
    review = Review.objects.create(activity=activity, decision=ReviewDecision.REJECTED)
    Comment.objects.create(activity=activity, author=teacher.user, text="Revise a introdução.")
    Comment.objects.create(activity=activity, author=student.user, text="Feito.")

    for user in (student.user, teacher.user):
        response = get_detail(client, user, activity.id)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "id",
            "title",
            "deadline",
            "status",
            "overdue",
            "student",
            "description",
            "created_at",
            "submissions",
            "reviews",
            "comments_count",
        }
        assert body["id"] == activity.id
        assert body["title"] == "Capítulo 2"
        assert body["description"] == "Entregar o capítulo 2 do TCC"
        assert body["status"] == "in_review"
        assert body["overdue"] is False
        assert body["student"] == {"id": student.user_id, "full_name": student.user.full_name}
        assert body["comments_count"] == 2
        assert [s["id"] for s in body["submissions"]] == [submission.id]
        assert set(body["submissions"][0]) == {"id", "submitted_at", "files"}
        assert [
            (f["file_name"], f["size_bytes"], f["content_type"])
            for f in body["submissions"][0]["files"]
        ] == [
            ("a.pdf", 10, "application/pdf"),
            ("b.zip", 20, "application/zip"),
        ]
        assert all(
            set(f) == {"id", "file_name", "size_bytes", "content_type"}
            for f in body["submissions"][0]["files"]
        )
        assert body["reviews"] == [
            {
                "id": review.id,
                "decision": "rejected",
                "decided_at": body["reviews"][0]["decided_at"],
            }
        ]
        assert body["reviews"][0]["decided_at"] is not None


def test_an_activity_without_submissions_reviews_or_comments(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    body = get_detail(client, student.user, activity.id).json()

    assert body["submissions"] == []
    assert body["reviews"] == []
    assert body["comments_count"] == 0


def test_the_detail_exposes_no_personal_data_or_storage_address(client):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    add_submission(activity)

    for user in (student.user, teacher.user):
        raw = get_detail(client, user, activity.id).content.decode()

        for secret in (
            student.user.email,
            student.registration_number,
            "storage_key",
            "activities/",
        ):
            assert secret not in raw


def test_the_detail_marks_an_open_activity_past_its_deadline_as_overdue(client):
    student, _, advising = advised_pair()
    late = add_activity(advising, deadline=timezone.localdate() - datetime.timedelta(days=1))
    on_the_day = add_activity(advising, deadline=timezone.localdate())
    done = add_activity(
        advising,
        status=ActivityStatus.COMPLETED,
        deadline=timezone.localdate() - datetime.timedelta(days=9),
    )

    flags = {
        a.id: get_detail(client, student.user, a.id).json()["overdue"]
        for a in (late, on_the_day, done)
    }

    assert flags == {late.id: True, on_the_day.id: False, done.id: False}


def test_the_same_404_for_everything_outside_the_scope_or_missing(client):
    student, teacher, advising = advised_pair()
    mine = add_activity(advising)
    other_student = make_student(course=student.course)
    other_advising = make_advising(
        student=other_student, teacher=make_teacher(courses=[student.course])
    )
    theirs = add_activity(other_advising)
    deleted = add_activity(advising, deleted_at=timezone.now())
    archived = add_activity(advising, status=ActivityStatus.ARCHIVED)
    ended_advising = make_advising(student=make_student(course=student.course), teacher=teacher)
    ended = add_activity(ended_advising, status=ActivityStatus.COMPLETED)
    Advising.objects.filter(pk=ended_advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    responses = [
        get_detail(client, student.user, theirs.id),
        get_detail(client, teacher.user, theirs.id),
        get_detail(client, student.user, deleted.id),
        get_detail(client, teacher.user, deleted.id),
        get_detail(client, student.user, archived.id),
        get_detail(client, teacher.user, archived.id),
        get_detail(client, teacher.user, ended.id),
        get_detail(client, student.user, 999999),
        get_detail(client, teacher.user, 999999),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert get_detail(client, student.user, mine.id).status_code == 200


def test_the_detail_of_a_student_who_left_the_advising_is_not_reachable_by_the_old_student(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=ActivityStatus.COMPLETED)
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    assert get_detail(client, student.user, activity.id).status_code == 404


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)

    assert get_detail(client, make_user(UserType.ADMIN), activity.id).status_code == 403
    assert client.get(detail_url(activity.id)).status_code == 401


def test_a_non_numeric_id_is_not_a_route(client):
    student, _, _ = advised_pair()

    assert get_detail(client, student.user, "abc").status_code in (404, 422)


def count_queries(client, user, activity_id):
    with CaptureQueriesContext(connection) as queries:
        response = get_detail(client, user, activity_id)
    assert response.status_code == 200
    return len(queries)


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_the_number_of_queries_does_not_grow_with_submissions_files_reviews_and_comments(
    client, role
):
    student, teacher, advising = advised_pair()
    user = student.user if role == "student" else teacher.user
    activity = add_activity(advising)
    add_submission(activity)
    Review.objects.create(activity=activity, decision=ReviewDecision.ACCEPTED)
    few = count_queries(client, user, activity.id)

    for _ in range(4):
        add_submission(activity, [("a.pdf", 1, "application/pdf"), ("b.pdf", 2, "application/pdf")])
        Review.objects.create(activity=activity, decision=ReviewDecision.REJECTED)
        Comment.objects.create(activity=activity, author=teacher.user, text="Comentário.")

    assert count_queries(client, user, activity.id) == few


def test_the_openapi_documents_the_detail_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/atividades/{activity_id}"][
        "get"
    ]

    assert {"200", "401", "403", "404"} <= set(operation["responses"])
