"""The advisor refuses an activity in review with 0 to 5 files attached to the refusal (RF46, RN48)."""

from unittest.mock import patch

import pytest
from django.core.files.storage import InMemoryStorage, storages
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.activities.models import (
    MAX_FILE_SIZE_BYTES,
    Activity,
    ActivityStatus,
    Review,
    ReviewDecision,
    ReviewFile,
)
from apps.activities.services import STORAGE_UNAVAILABLE_MESSAGE
from apps.activities.uploads import MISMATCH_MESSAGE, NOT_ALLOWED_MESSAGE, TOO_BIG_MESSAGE
from apps.advising.models import Advising, AdvisingStatus
from apps.notifications.models import Notification
from tests.api.helpers import bearer, errors_by_field, post_json
from tests.api.uploads import DOC, DOCX, JPG, PDF, PNG, ZIP, upload
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

JUSTIFICATION = "Refazer a introdução e citar as fontes."
COUNT_MESSAGE = "Anexe até 5 arquivos à recusa."
NOT_ALLOWED = NOT_ALLOWED_MESSAGE
TOO_BIG = TOO_BIG_MESSAGE
MISMATCH = MISMATCH_MESSAGE
STORAGE_DOWN = {"detail": STORAGE_UNAVAILABLE_MESSAGE}


def refuse_url(activity_id):
    return f"/api/atividades/{activity_id}/recusar"


def refuse(client, user, activity_id, justification=JUSTIFICATION, files=()):
    data = {"files": list(files)}
    if justification is not None:
        data["justification"] = justification
    return client.post(refuse_url(activity_id), data=data, headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def in_review(advising, status=ActivityStatus.IN_REVIEW, **extra):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline="2099-12-31",
        status=status,
        **extra,
    )


def status_of(activity):
    activity.refresh_from_db()
    return activity.status


def stored_keys(folder=""):
    """Every file left in the in-memory storage, in any folder."""
    storage = storages["submissions"]
    folders, files = storage.listdir(folder)
    keys = [f"{folder}/{name}".lstrip("/") for name in files]
    for name in folders:
        keys += stored_keys(f"{folder}/{name}".lstrip("/"))
    return keys


def assert_nothing_was_recorded(activity):
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()
    assert not ReviewFile.objects.exists()
    assert not Notification.objects.exists()
    assert stored_keys() == []


def test_the_teacher_refuses_with_two_files_and_the_activity_goes_back_to_pending(client):
    student, teacher, advising = advised_pair()
    activity = in_review(advising)

    response = refuse(
        client, teacher.user, activity.id, files=[upload("notas.pdf", PDF), upload("foto.png", PNG)]
    )

    assert response.status_code == 200
    assert status_of(activity) == ActivityStatus.PENDING
    review = Review.objects.get()
    assert (review.decision, review.justification) == (ReviewDecision.REJECTED, JUSTIFICATION)
    files = list(review.files.order_by("id"))
    assert [(f.file_name, f.size_bytes, f.content_type) for f in files] == [
        ("notas.pdf", len(PDF), "application/pdf"),
        ("foto.png", len(PNG), "image/png"),
    ]
    with storages["submissions"].open(files[0].storage_key) as handle:
        assert handle.read() == PDF


def test_the_detail_lists_the_attachments_of_each_decision(client):
    student, teacher, advising = advised_pair()
    activity = in_review(advising)
    refuse(client, teacher.user, activity.id, files=[upload("notas.pdf", PDF)])

    reviews = client.get(f"/api/atividades/{activity.id}", headers=bearer(student.user)).json()[
        "reviews"
    ]

    assert len(reviews) == 1
    assert reviews[0]["files"] == [
        {
            "id": ReviewFile.objects.get().id,
            "file_name": "notas.pdf",
            "size_bytes": len(PDF),
            "content_type": "application/pdf",
        }
    ]


def test_a_refusal_without_files_is_accepted_and_has_no_attachments(client):
    student, teacher, advising = advised_pair()
    activity = in_review(advising)

    response = refuse(client, teacher.user, activity.id)

    assert response.status_code == 200
    assert status_of(activity) == ActivityStatus.PENDING
    assert not ReviewFile.objects.exists()
    reviews = client.get(f"/api/atividades/{activity.id}", headers=bearer(student.user)).json()[
        "reviews"
    ]
    assert reviews[0]["files"] == []


def test_the_student_is_notified_as_before_and_the_payload_does_not_change(client):
    student, teacher, advising = advised_pair()
    activity = in_review(advising)

    refuse(client, teacher.user, activity.id, files=[upload("notas.pdf", PDF)])

    notification = Notification.objects.get(recipient=student.user)
    assert notification.payload == {
        "activity_title": "Capítulo 2",
        "decision": "rejected",
        "teacher_name": teacher.user.full_name,
        "justification": JUSTIFICATION,
    }


def test_every_allowed_type_is_accepted_up_to_five_files(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)
    files = [
        upload("a.pdf", PDF),
        upload("b.doc", DOC),
        upload("c.docx", DOCX),
        upload("d.zip", ZIP),
        upload("e.jpg", JPG),
    ]

    response = refuse(client, teacher.user, activity.id, files=files)

    assert response.status_code == 200
    assert ReviewFile.objects.count() == 5


def test_more_than_five_files_are_rejected_and_nothing_is_recorded(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)
    files = [upload(f"{number}.pdf", PDF) for number in range(6)]

    response = refuse(client, teacher.user, activity.id, files=files)

    assert response.status_code == 422
    assert errors_by_field(response) == {"files": COUNT_MESSAGE}
    assert_nothing_was_recorded(activity)


@pytest.mark.parametrize(
    ("file", "message"),
    [
        (lambda: upload("virus.exe", b"MZ"), NOT_ALLOWED),
        (lambda: upload("grande.pdf", PDF + b"0" * MAX_FILE_SIZE_BYTES), TOO_BIG),
        (lambda: upload("falso.pdf", b"isto nao e um pdf"), MISMATCH),
    ],
)
def test_one_bad_file_rejects_the_whole_refusal_with_the_message_of_the_delivery(
    client, file, message
):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)

    response = refuse(client, teacher.user, activity.id, files=[upload("bom.pdf", PDF), file()])

    assert response.status_code == 422
    assert errors_by_field(response) == {"files.1": message}
    assert_nothing_was_recorded(activity)


@pytest.mark.parametrize("justification", [None, "", "   \n\t "])
def test_the_justification_is_still_required_even_with_files(client, justification):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)

    response = refuse(
        client, teacher.user, activity.id, justification, files=[upload("a.pdf", PDF)]
    )

    assert response.status_code == 422
    assert "justification" in errors_by_field(response)
    assert_nothing_was_recorded(activity)


def test_a_justification_of_500_characters_is_accepted_and_501_is_rejected(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)

    too_long = refuse(client, teacher.user, activity.id, "a" * 501)
    just_right = refuse(client, teacher.user, activity.id, "  " + "a" * 500 + "  ")

    assert too_long.status_code == 422
    assert just_right.status_code == 200
    assert Review.objects.get().justification == "a" * 500


def fail_on_the_nth_save(failing_call):
    calls = []
    original = InMemoryStorage.save

    def save(self, name, content, max_length=None):
        calls.append(name)
        if len(calls) == failing_call:
            raise OSError("o armazenamento caiu")
        return original(self, name, content, max_length)

    return patch.object(InMemoryStorage, "save", save)


def test_a_storage_failure_records_nothing_keeps_the_activity_in_review_and_removes_the_files(
    client,
):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)
    files = [upload("a.pdf", PDF), upload("b.pdf", PDF), upload("c.pdf", PDF)]

    with fail_on_the_nth_save(3):
        response = refuse(client, teacher.user, activity.id, files=files)

    assert response.status_code == 503
    assert response.json() == STORAGE_DOWN
    assert_nothing_was_recorded(activity)


def test_a_failing_cleanup_does_not_hide_the_storage_failure(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)
    files = [upload("a.pdf", PDF), upload("b.pdf", PDF)]

    with (
        fail_on_the_nth_save(2),
        patch.object(InMemoryStorage, "delete", side_effect=OSError("não apagou")),
    ):
        response = refuse(client, teacher.user, activity.id, files=files)

    assert response.status_code == 503
    assert status_of(activity) == ActivityStatus.IN_REVIEW
    assert not Review.objects.exists()


@pytest.mark.parametrize(
    "status", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.COMPLETED]
)
def test_an_activity_that_is_not_in_review_answers_409_and_removes_the_files(client, status):
    _, teacher, advising = advised_pair()
    activity = in_review(advising, status=status)

    response = refuse(client, teacher.user, activity.id, files=[upload("a.pdf", PDF)])

    assert response.status_code == 409
    assert response.json() == {"detail": "Só uma atividade em revisão pode ser revisada."}
    assert not Review.objects.exists()
    assert stored_keys() == []


def test_the_student_is_forbidden_and_a_visitor_is_unauthorized(client):
    student, _, advising = advised_pair()
    activity = in_review(advising)

    assert refuse(client, student.user, activity.id).status_code == 403
    assert client.post(refuse_url(activity.id), data={"justification": "x"}).status_code == 401
    assert_nothing_was_recorded(activity)


def test_another_teacher_an_unknown_id_and_unreachable_activities_answer_the_same_404(client):
    _, teacher, advising = advised_pair()
    stranger = make_teacher()
    deleted = in_review(advising, deleted_at=timezone.now())
    archived = in_review(advising, status=ActivityStatus.ARCHIVED)
    activity = in_review(advising)
    unknown = refuse(client, teacher.user, activity.id + 1000)

    other = refuse(client, stranger.user, activity.id)

    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json()
    assert refuse(client, teacher.user, deleted.id).status_code == 404
    assert refuse(client, teacher.user, archived.id).status_code == 404
    assert status_of(activity) == ActivityStatus.IN_REVIEW


def test_an_ended_advising_answers_404_and_removes_the_files(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )

    response = refuse(client, teacher.user, activity.id, files=[upload("a.pdf", PDF)])

    assert response.status_code == 404
    assert not Review.objects.exists()
    assert stored_keys() == []


def test_an_acceptance_cannot_carry_files_and_nothing_changes(client):
    _, teacher, advising = advised_pair()
    activity = in_review(advising)

    response = client.post(
        f"/api/atividades/{activity.id}/revisar",
        data={"decision": "accepted", "files": [upload("a.pdf", PDF)]},
        headers=bearer(teacher.user),
    )

    assert response.status_code in {400, 422}
    assert_nothing_was_recorded(activity)


def test_the_acceptance_and_the_json_refusal_keep_working_without_attachments(client):
    student, teacher, advising = advised_pair()
    refused = in_review(advising)
    accepted = in_review(advising)

    json_refusal = post_json(
        client,
        f"/api/atividades/{refused.id}/revisar",
        {"decision": "rejected", "justification": JUSTIFICATION},
        headers=bearer(teacher.user),
    )
    acceptance = post_json(
        client,
        f"/api/atividades/{accepted.id}/revisar",
        {"decision": "accepted"},
        headers=bearer(teacher.user),
    )

    assert (json_refusal.status_code, acceptance.status_code) == (200, 200)
    assert not ReviewFile.objects.exists()
    for activity in (refused, accepted):
        reviews = client.get(f"/api/atividades/{activity.id}", headers=bearer(student.user)).json()[
            "reviews"
        ]
        assert reviews[0]["files"] == []


def test_the_queries_of_the_detail_do_not_grow_with_the_attachments(client):
    student, teacher, advising = advised_pair()
    activity = in_review(advising)
    refuse(client, teacher.user, activity.id, files=[upload("a.pdf", PDF)])
    headers = bearer(student.user)

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert client.get(f"/api/atividades/{activity.id}", headers=headers).status_code == 200
        return len(captured)

    few = queries()
    for number in range(4):
        review = Review.objects.create(
            activity=activity, decision=ReviewDecision.REJECTED, justification="Outra rodada."
        )
        for index in range(3):
            ReviewFile.objects.create(
                review=review,
                file_name=f"{number}-{index}.pdf",
                size_bytes=10,
                content_type="application/pdf",
                storage_key=f"revisoes/{activity.id}/{number}-{index}",
            )

    assert queries() == few


def test_the_openapi_documents_the_refusal_with_files(client):
    schema = client.get("/api/openapi.json").json()

    operation = schema["paths"]["/api/atividades/{activity_id}/recusar"]["post"]
    assert operation["description"]
    assert {"200", "401", "403", "404", "409", "422", "503"} <= set(operation["responses"])
    form = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    properties = (
        schema["components"]["schemas"][form["$ref"].rsplit("/", 1)[-1]]["properties"]
        if "$ref" in form
        else form["properties"]
    )
    assert {"justification", "files"} <= set(properties)
    detail = schema["components"]["schemas"]["ReviewOut"]["properties"]
    assert "files" in detail
