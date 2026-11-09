import pytest
from django.core.files.storage import storages
from django.test import Client
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus, SubmissionFile
from apps.advising.models import Advising
from apps.advising.services import close_advising
from tests.api.helpers import bearer
from tests.api.uploads import PDF, PNG, upload
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

NOT_FOUND = {"detail": "Arquivo não encontrado."}


def download_url(activity_id, file_id):
    return f"/api/atividades/{activity_id}/arquivos/{file_id}"


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def add_activity(advising, status=ActivityStatus.PENDING):
    return Activity.objects.create(
        advising=advising,
        title="Capítulo 2",
        description="Entregar o capítulo 2 do TCC",
        deadline="2099-12-31",
        status=status,
    )


def deliver(client, student, activity, files):
    response = client.post(
        f"/api/atividades/{activity.id}/entregas",
        data={"files": files},
        headers=bearer(student.user),
    )
    assert response.status_code == 201
    return [item["id"] for item in response.json()["files"]]


def get(client, user, activity_id, file_id):
    headers = bearer(user) if user else {}
    return client.get(download_url(activity_id, file_id), headers=headers)


def delivered_pdf(client, name="relatorio.pdf"):
    student, teacher, advising = advised_pair()
    activity = add_activity(advising)
    (file_id,) = deliver(client, student, activity, [upload(name, PDF)])
    return student, teacher, activity, file_id


@pytest.mark.parametrize("who", ["student", "advisor"])
def test_the_student_and_the_active_advisor_download_the_file_in_streaming(client, who):
    student, teacher, activity, file_id = delivered_pdf(client)
    user = student.user if who == "student" else teacher.user

    response = get(client, user, activity.id, file_id)

    assert response.status_code == 200
    assert response.streaming
    assert b"".join(response.streaming_content) == PDF
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'attachment; filename="relatorio.pdf"'
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_route_itself_sends_nosniff_even_when_the_global_setting_is_off(client, settings):
    student, _, activity, file_id = delivered_pdf(client)
    settings.SECURE_CONTENT_TYPE_NOSNIFF = False

    response = get(Client(), student.user, activity.id, file_id)

    assert response.status_code == 200
    assert response["X-Content-Type-Options"] == "nosniff"


def test_the_stored_type_is_used_not_a_guess_from_the_content(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    (file_id,) = deliver(client, student, activity, [upload("grafico.png", PNG, "text/html")])

    response = get(client, student.user, activity.id, file_id)

    assert response["Content-Type"] == "image/png"


def test_a_name_with_accents_is_encoded_in_the_content_disposition(client):
    student, _, activity, file_id = delivered_pdf(client, "relatório final.pdf")

    response = get(client, student.user, activity.id, file_id)

    assert "attachment" in response["Content-Disposition"]
    assert "filename*=utf-8''relat%C3%B3rio%20final.pdf" in response["Content-Disposition"]


@pytest.mark.parametrize("status", [ActivityStatus.IN_REVIEW, ActivityStatus.COMPLETED])
@pytest.mark.parametrize("who", ["student", "advisor"])
def test_the_download_works_in_any_status(client, status, who):
    student, teacher, activity, file_id = delivered_pdf(client)
    Activity.objects.filter(pk=activity.pk).update(status=status)
    user = student.user if who == "student" else teacher.user

    response = get(client, user, activity.id, file_id)

    assert response.status_code == 200


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("who", ["student", "advisor"])
@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404(client, hide, who):
    student, teacher, activity, file_id = delivered_pdf(client)
    hide(activity)
    user = student.user if who == "student" else teacher.user

    response = get(client, user, activity.id, file_id)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("who", ["other_student", "other_teacher", "loner"])
def test_anyone_else_gets_the_same_404(client, who):
    _, _, activity, file_id = delivered_pdf(client)
    other_pair = make_advising(student=make_student(), teacher=make_teacher())
    user = {
        "other_student": other_pair.student.user,
        "other_teacher": other_pair.teacher.user,
        "loner": make_student().user,
    }[who]

    response = get(client, user, activity.id, file_id)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_a_file_of_another_activity_gets_the_same_404(client):
    student, _, advising = advised_pair()
    first = add_activity(advising)
    second = add_activity(advising)
    (file_of_first,) = deliver(client, student, first, [upload("a.pdf", PDF)])

    response = get(client, student.user, second.id, file_of_first)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("which", ["activity", "file"])
def test_a_missing_activity_or_file_gets_the_same_404(client, which):
    student, _, activity, file_id = delivered_pdf(client)

    response = get(
        client,
        student.user,
        999999 if which == "activity" else activity.id,
        file_id if which == "activity" else 999999,
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_a_file_that_is_gone_from_the_storage_gets_the_same_404(client):
    student, _, activity, file_id = delivered_pdf(client)
    storages["submissions"].delete(SubmissionFile.objects.get(pk=file_id).storage_key)

    response = get(client, student.user, activity.id, file_id)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_an_admin_gets_a_403(client):
    _, _, activity, file_id = delivered_pdf(client)

    response = get(client, make_user(UserType.ADMIN), activity.id, file_id)

    assert response.status_code == 403


def test_an_anonymous_request_gets_a_401(client):
    _, _, activity, file_id = delivered_pdf(client)

    response = get(client, None, activity.id, file_id)

    assert response.status_code == 401


def test_the_openapi_documents_the_download_as_a_binary_attachment_and_its_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"][
        "/api/atividades/{activity_id}/arquivos/{file_id}"
    ]["get"]

    binary = operation["responses"]["200"]["content"]["application/octet-stream"]["schema"]
    assert binary == {"type": "string", "format": "binary"}
    assert {"401", "403", "404"} <= set(operation["responses"])
    assert "streaming" in operation["description"]
