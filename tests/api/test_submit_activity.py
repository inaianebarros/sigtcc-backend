import threading
from unittest.mock import patch

import pytest
from django.core.files.storage import InMemoryStorage, storages
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import (
    MAX_FILE_SIZE_BYTES,
    Activity,
    ActivityStatus,
    Submission,
    SubmissionFile,
)
from apps.advising.models import Advising, AdvisingStatus
from apps.advising.services import close_advising
from tests.api.helpers import (
    bearer,
    errors_by_field,
    send_while_the_advising_ends,
    wait_for_a_request_blocked_on_a_lock,
)
from tests.api.uploads import DOC, DOCX, JPG, PDF, PNG, VALID_FILES, ZIP, upload, zip_bytes
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db


def url(activity_id):
    return f"/api/atividades/{activity_id}/entregas"


def deliver(client, user, activity_id, files):
    return client.post(url(activity_id), data={"files": files}, headers=bearer(user))


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
        deadline="2099-12-31",
        status=status,
        **extra,
    )


def stored(key):
    with storages["submissions"].open(key) as handle:
        return handle.read()


def test_the_student_delivers_a_file_and_gets_201_with_the_metadata(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "submitted_at", "files"}
    assert len(body["files"]) == 1
    delivered = body["files"][0]
    assert set(delivered) == {"id", "file_name", "size_bytes", "content_type"}
    assert delivered["file_name"] == "relatorio.pdf"
    assert delivered["size_bytes"] == len(PDF)
    assert delivered["content_type"] == "application/pdf"
    assert Submission.objects.get(pk=body["id"]).activity_id == activity.id


@pytest.mark.parametrize("status", [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS])
def test_delivering_does_not_change_the_status(client, status):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=status)

    response = deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 201
    activity.refresh_from_db()
    assert activity.status == status


def test_five_files_make_one_submission_with_every_allowed_type_normalized(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    names = ["relatorio.pdf", "capitulo.doc", "capitulo.docx", "codigo.zip", "grafico.png"]
    files = [upload(name, VALID_FILES[name][0]) for name in names]

    response = deliver(client, student.user, activity.id, files)

    assert response.status_code == 201
    delivered = response.json()["files"]
    assert [item["file_name"] for item in delivered] == names
    assert [item["content_type"] for item in delivered] == [VALID_FILES[n][1] for n in names]
    assert Submission.objects.count() == 1
    assert SubmissionFile.objects.count() == 5


def test_a_jpg_is_accepted_and_stored_as_image_jpeg(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload("foto.jpg", JPG)])

    assert response.json()["files"][0]["content_type"] == "image/jpeg"


def test_the_content_type_sent_by_the_client_is_ignored(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload("grafico.png", PNG, "text/html")])

    assert response.status_code == 201
    assert response.json()["files"][0]["content_type"] == "image/png"
    assert SubmissionFile.objects.get().content_type == "image/png"


def test_the_storage_key_is_generated_by_the_server_and_holds_the_bytes(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    deliver(
        client,
        student.user,
        activity.id,
        [upload("segredo.pdf", PDF), upload("outro.pdf", PDF + b"x")],
    )

    keys = list(SubmissionFile.objects.order_by("id").values_list("storage_key", flat=True))
    assert len(set(keys)) == 2
    for key in keys:
        assert key.startswith(f"entregas/{activity.id}/")
        assert "segredo" not in key and "outro" not in key and ".pdf" not in key
    assert [stored(key) for key in keys] == [PDF, PDF + b"x"]


@pytest.mark.parametrize("name", ["../../etc/relatorio.pdf", "C:\\Users\\aluno\\relatorio.pdf"])
def test_the_original_name_is_kept_without_any_path(client, name):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload(name, PDF)])

    assert response.status_code == 201
    assert response.json()["files"][0]["file_name"] == "relatorio.pdf"


def test_the_delivery_shows_up_in_the_activity_detail(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)])

    detail = client.get(f"/api/atividades/{activity.id}", headers=bearer(student.user)).json()

    assert [item["file_name"] for item in detail["submissions"][0]["files"]] == ["relatorio.pdf"]


def stored_keys():
    storage = storages["submissions"]
    try:
        directories, _ = storage.listdir("entregas")
    except FileNotFoundError:
        return []
    keys = []
    for directory in directories:
        _, names = storage.listdir(f"entregas/{directory}")
        keys += [f"entregas/{directory}/{name}" for name in names]
    return keys


def assert_nothing_was_recorded():
    assert Submission.objects.count() == 0
    assert SubmissionFile.objects.count() == 0
    assert stored_keys() == []


EMPTY = "O arquivo está vazio."
TOO_BIG = "O arquivo excede o limite de 10 MB."
NOT_ALLOWED = "Tipo de arquivo não permitido. Envie PDF, DOC, DOCX, ZIP, PNG ou JPG."
MISMATCH = "O conteúdo do arquivo não corresponde ao tipo informado pela extensão."
HTML = b"<html><script>alert(1)</script></html>"


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("vazio.pdf", b"", EMPTY),
        ("grande.pdf", PDF + b"0" * (MAX_FILE_SIZE_BYTES - len(PDF) + 1), TOO_BIG),
        ("programa.exe", b"MZ\x90\x00", NOT_ALLOWED),
        ("pagina.html", HTML, NOT_ALLOWED),
        ("sem_extensao", PDF, NOT_ALLOWED),
        (".pdf", PDF, NOT_ALLOWED),
        ("relatorio.pdf.exe", PDF, NOT_ALLOWED),
        ("falso.pdf", PNG, MISMATCH),
        ("falso.png", HTML, MISMATCH),
        ("falso.jpg", PDF, MISMATCH),
        ("falso.zip", PDF, MISMATCH),
        ("falso.doc", PDF, MISMATCH),
        ("falso.docx", ZIP, MISMATCH),
        ("falso.docx", PDF, MISMATCH),
        ("planilha.docx", zip_bytes(("[Content_Types].xml", "xl/workbook.xml")), MISMATCH),
        ("poliglota.zip", PDF + ZIP, MISMATCH),
    ],
)
def test_an_invalid_file_rejects_the_whole_submission_and_nothing_is_stored(
    client, name, content, message
):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(
        client, student.user, activity.id, [upload("bom.pdf", PDF), upload(name, content)]
    )

    assert response.status_code == 422
    assert errors_by_field(response) == {"files.1": message}
    assert_nothing_was_recorded()


def test_every_invalid_file_is_reported_by_its_position(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    files = [upload("falso.png", PDF), upload("bom.pdf", PDF), upload("vazio.pdf", b"")]

    response = deliver(client, student.user, activity.id, files)

    assert response.status_code == 422
    assert errors_by_field(response) == {"files.0": MISMATCH, "files.2": EMPTY}
    assert_nothing_was_recorded()


def test_a_sixth_file_rejects_the_whole_submission(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(
        client, student.user, activity.id, [upload(f"{i}.pdf", PDF) for i in range(6)]
    )

    assert response.status_code == 422
    assert errors_by_field(response) == {"files": "Envie de 1 a 5 arquivos por entrega."}
    assert_nothing_was_recorded()


def test_a_delivery_without_the_files_field_is_a_validation_error(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = client.post(url(activity.id), data={}, headers=bearer(student.user))

    assert response.status_code == 422
    assert "files" in errors_by_field(response)
    assert_nothing_was_recorded()


def test_the_limits_themselves_are_accepted(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    biggest = PDF + b"0" * (MAX_FILE_SIZE_BYTES - len(PDF))
    longest = "a" * 251 + ".pdf"

    response = deliver(
        client, student.user, activity.id, [upload("limite.pdf", biggest), upload(longest, PDF)]
    )

    assert response.status_code == 201
    assert [item["size_bytes"] for item in response.json()["files"]] == [
        MAX_FILE_SIZE_BYTES,
        len(PDF),
    ]
    assert response.json()["files"][1]["file_name"] == longest


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("codigo.zip", zip_bytes(("a/b.txt",))),
        ("vazio.zip", zip_bytes(())),
        ("capitulo.docx", DOCX),
        ("capitulo.doc", DOC),
        ("FOTO.JPG", JPG),
        ("foto.jpeg", JPG),
        ("modelo.docx.zip", DOCX),
    ],
)
def test_other_valid_shapes_are_accepted(client, name, content):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload(name, content)])

    assert response.status_code == 201


def test_a_name_over_255_characters_arrives_cut_by_django_keeping_its_extension(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload("a" * 300 + ".pdf", PDF)])

    assert response.status_code == 201
    file_name = response.json()["files"][0]["file_name"]
    assert len(file_name) == 255
    assert file_name.endswith(".pdf")


NOT_FOUND = {"detail": "Atividade não encontrada."}


def another_student_with_an_advising():
    return make_advising(student=make_student(), teacher=make_teacher()).student


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            ActivityStatus.IN_REVIEW,
            "Esta atividade está em revisão e não aceita novas entregas.",
        ),
        (
            ActivityStatus.COMPLETED,
            "Esta atividade já foi concluída e não aceita novas entregas.",
        ),
    ],
)
def test_an_activity_in_review_or_completed_gets_a_409_and_nothing_is_stored(
    client, status, message
):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=status)

    response = deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 409
    assert response.json() == {"detail": message}
    assert_nothing_was_recorded()
    activity.refresh_from_db()
    assert activity.status == status


def hide_it(activity):
    Activity.objects.filter(pk=activity.pk).update(deleted_at=timezone.now())


def archive_it(activity):
    Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.ARCHIVED)


def end_the_advising(activity):
    close_advising(Advising.objects.get(pk=activity.advising_id), "Encerrada para o teste.")


@pytest.mark.parametrize("hide", [hide_it, archive_it, end_the_advising])
def test_a_deleted_archived_or_ended_activity_gets_the_same_404(client, hide):
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    hide(activity)

    response = deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert_nothing_was_recorded()


def test_a_missing_activity_gets_the_same_404(client):
    student, _, _ = advised_pair()

    response = deliver(client, student.user, 999999, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_the_activity_of_another_student_gets_the_same_404_and_nothing_is_stored(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    other = another_student_with_an_advising()

    response = deliver(client, other.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert_nothing_was_recorded()


def test_a_student_with_no_advising_gets_the_same_404(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)
    loner = make_student()

    response = deliver(client, loner.user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("who", ["advisor", "admin"])
def test_a_teacher_or_an_admin_gets_a_403(client, who):
    _, teacher, advising = advised_pair()
    activity = add_activity(advising)
    user = teacher.user if who == "advisor" else make_user(UserType.ADMIN)

    response = deliver(client, user, activity.id, [upload("relatorio.pdf", PDF)])

    assert response.status_code == 403
    assert_nothing_was_recorded()


def test_an_anonymous_request_gets_a_401(client):
    _, _, advising = advised_pair()
    activity = add_activity(advising)

    response = client.post(url(activity.id), data={"files": [upload("relatorio.pdf", PDF)]})

    assert response.status_code == 401
    assert_nothing_was_recorded()


@pytest.mark.parametrize("who", ["other", "in_review", "missing"])
def test_the_files_are_validated_before_the_activity_is_looked_up(client, who):
    student, _, advising = advised_pair()
    activity = add_activity(
        advising,
        status=ActivityStatus.IN_REVIEW if who == "in_review" else ActivityStatus.PENDING,
    )
    user = another_student_with_an_advising().user if who == "other" else student.user
    activity_id = 999999 if who == "missing" else activity.id

    response = deliver(client, user, activity_id, [upload("vazio.pdf", b"")])

    assert response.status_code == 422
    assert errors_by_field(response) == {"files.0": EMPTY}


@pytest.mark.django_db(transaction=True)
def test_an_advising_that_ends_during_the_action_gets_the_404_and_nothing_is_stored(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_advising_ends(
        advising,
        lambda: deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)]),
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert_nothing_was_recorded()
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.ARCHIVED
    assert Advising.objects.get(pk=advising.pk).status == AdvisingStatus.ENDED


def count_queries(client, user, activity_id, count):
    files = [upload(f"{index}.pdf", PDF) for index in range(count)]
    with CaptureQueriesContext(connection) as queries:
        response = deliver(client, user, activity_id, files)
    assert response.status_code == 201
    return len(queries)


def test_the_number_of_queries_does_not_depend_on_the_number_of_files(client):
    student, _, advising = advised_pair()
    first = add_activity(advising)
    second = add_activity(advising)
    count_queries(client, student.user, first.id, 1)  # warms up the caches

    with_one = count_queries(client, student.user, second.id, 1)
    with_five = count_queries(client, student.user, add_activity(advising).id, 5)

    assert with_five == with_one


STORAGE_DOWN = {"detail": "Não foi possível guardar os arquivos. Tente novamente."}


def three_files():
    return [upload(f"{index}.pdf", PDF + bytes([index])) for index in range(3)]


def fail_on_the_nth_save(failing_call):
    original = InMemoryStorage.save
    calls = []

    def save(self, name, content, max_length=None):
        calls.append(name)
        if len(calls) == failing_call:
            raise OSError("o armazenamento caiu")
        return original(self, name, content, max_length)

    return patch.object(InMemoryStorage, "save", save)


def test_a_storage_failure_leaves_no_record_and_removes_the_files_already_stored(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    with fail_on_the_nth_save(3):
        response = deliver(client, student.user, activity.id, three_files())

    assert response.status_code == 503
    assert response.json() == STORAGE_DOWN
    assert_nothing_was_recorded()


def test_a_failing_cleanup_does_not_hide_the_storage_failure(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    with (
        fail_on_the_nth_save(2),
        patch.object(InMemoryStorage, "delete", side_effect=OSError("não apagou")),
    ):
        response = deliver(client, student.user, activity.id, three_files())

    assert response.status_code == 503
    assert response.json() == STORAGE_DOWN
    assert Submission.objects.count() == 0


def test_a_database_failure_removes_every_file_already_stored():
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    client = Client(raise_request_exception=False)

    with patch.object(SubmissionFile, "objects") as manager:
        manager.bulk_create.side_effect = DatabaseError("o banco caiu")
        response = deliver(client, student.user, activity.id, three_files())

    assert response.status_code == 500
    assert_nothing_was_recorded()


@pytest.mark.django_db(transaction=True)
def test_a_failure_when_the_transaction_commits_also_removes_the_files():
    student, _, advising = advised_pair()
    activity = add_activity(advising)
    client = Client(raise_request_exception=False)
    original = InMemoryStorage.save

    def save_and_dangle_the_reference(self, name, content, max_length=None):
        # The foreign key is deferred: the database only refuses this when the transaction commits.
        Submission.objects.update(activity_id=0)
        return original(self, name, content, max_length)

    with patch.object(InMemoryStorage, "save", save_and_dangle_the_reference):
        response = deliver(client, student.user, activity.id, three_files())

    assert response.status_code == 500
    assert_nothing_was_recorded()


def test_the_openapi_documents_the_multipart_files_the_limits_and_the_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"][
        "/api/atividades/{activity_id}/entregas"
    ]["post"]

    files = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"][
        "files"
    ]
    assert files["type"] == "array"
    assert files["items"] == {"format": "binary", "type": "string"}
    assert {"201", "401", "403", "404", "409", "422", "503"} <= set(operation["responses"])
    for limit in (
        "1 a 5 arquivos",
        "10 MB",
        "PDF, DOC, DOCX, ZIP, PNG e JPG (ou JPEG)",
        "Um arquivo inválido rejeita a entrega inteira",
    ):
        assert limit in operation["description"]


def test_the_name_is_trimmed_at_the_edges(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = deliver(client, student.user, activity.id, [upload("  relatorio.pdf  ", PDF)])

    assert response.status_code == 201
    assert response.json()["files"][0]["file_name"] == "relatorio.pdf"


def send_while_the_activity_goes_to_review(activity, send_request):
    """Sends a request that must wait for the activity lock, then sends the activity to review.

    A second transaction holds the activity row; once a request is blocked on it, that
    transaction puts the activity in review and commits, so the request runs after the change.
    Needs a test marked `django_db(transaction=True)`.
    """
    holding_the_lock = threading.Event()
    failures = []

    def change_the_status_while_holding_the_lock():
        try:
            with transaction.atomic():
                Activity.objects.select_for_update().get(pk=activity.pk)
                holding_the_lock.set()
                wait_for_a_request_blocked_on_a_lock()
                Activity.objects.filter(pk=activity.pk).update(status=ActivityStatus.IN_REVIEW)
        except Exception as error:  # noqa: BLE001 - reported to the main thread
            failures.append(error)
            holding_the_lock.set()
        finally:
            connection.close()

    changing = threading.Thread(target=change_the_status_while_holding_the_lock)
    changing.start()
    assert holding_the_lock.wait(timeout=10)
    response = send_request()
    changing.join(timeout=10)
    assert failures == []
    return response


@pytest.mark.django_db(transaction=True)
def test_a_status_change_that_commits_while_the_delivery_waits_gets_the_409_and_nothing_is_stored(
    client,
):
    student, _, advising = advised_pair()
    activity = add_activity(advising)

    response = send_while_the_activity_goes_to_review(
        activity,
        lambda: deliver(client, student.user, activity.id, [upload("relatorio.pdf", PDF)]),
    )

    assert response.status_code == 409
    assert_nothing_was_recorded()
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.IN_REVIEW
