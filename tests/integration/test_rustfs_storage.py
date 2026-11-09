"""Integration with the real RustFS of the Compose (ADR 0002).

The API tests use an in-memory storage; this module is the only one that reaches the bucket. It
is skipped, with the reason, when RustFS is not reachable: start it with `docker compose up -d
rustfs` (the host port 9000 of the Compose is enough).
"""

import socket
import uuid
from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command

from apps.activities.models import Activity, SubmissionFile
from apps.activities.storage import submission_storage
from tests.api.helpers import bearer
from tests.api.uploads import PDF, upload
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = [pytest.mark.integration]

CHUNK = 64 * 1024


@pytest.fixture(scope="module", autouse=True)
def rustfs_is_up():
    endpoint = settings.STORAGES["submissions"]["OPTIONS"]["endpoint_url"]
    address = urlparse(endpoint)
    try:
        socket.create_connection((address.hostname, address.port), timeout=2).close()
    except OSError:
        pytest.skip(f"RustFS fora do ar em {endpoint}; suba o Compose para rodar este teste.")


@pytest.fixture(autouse=True)
def the_bucket_exists(rustfs_is_up):
    call_command("ensure_storage_bucket")


@pytest.fixture
def keys_to_remove():
    keys = []
    yield keys
    storage = submission_storage()
    for key in keys:
        storage.delete(key)


def test_ensure_storage_bucket_creates_the_bucket_once_and_can_run_again():
    storage = submission_storage()
    client = storage.connection.meta.client

    call_command("ensure_storage_bucket")
    call_command("ensure_storage_bucket")

    names = [bucket["Name"] for bucket in client.list_buckets()["Buckets"]]
    assert storage.bucket_name in names


def test_a_file_is_sent_read_in_streaming_and_deleted(keys_to_remove):
    storage = submission_storage()
    content = b"%PDF-1.4\n" + bytes(range(256)) * 12_000  # about 3 MB, several chunks
    key = f"integration-tests/{uuid.uuid4().hex}"

    saved = storage.save(key, ContentFile(content))
    keys_to_remove.append(saved)

    assert saved == key
    assert storage.exists(saved)
    assert storage.size(saved) == len(content)
    with storage.open(saved, "rb") as handle:
        received = b"".join(iter(lambda: handle.read(CHUNK), b""))
    assert received == content

    storage.delete(saved)
    assert not storage.exists(saved)


def test_a_missing_object_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        submission_storage().open(f"integration-tests/{uuid.uuid4().hex}", "rb")


@pytest.mark.django_db
def test_a_delivery_and_its_download_go_through_the_real_bucket(client, keys_to_remove):
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    advising = make_advising(student=student, teacher=teacher)
    activity = Activity.objects.create(
        advising=advising, title="Capítulo 2", description="Entregar", deadline="2099-12-31"
    )

    delivered = client.post(
        f"/api/atividades/{activity.id}/entregas",
        data={"files": [upload("relatório.pdf", PDF)]},
        headers=bearer(student.user),
    )
    stored = SubmissionFile.objects.get()
    keys_to_remove.append(stored.storage_key)

    assert delivered.status_code == 201
    assert submission_storage().exists(stored.storage_key)

    downloaded = client.get(
        f"/api/atividades/{activity.id}/arquivos/{stored.id}", headers=bearer(teacher.user)
    )

    assert downloaded.status_code == 200
    assert downloaded.streaming
    assert b"".join(downloaded.streaming_content) == PDF
    assert downloaded["X-Content-Type-Options"] == "nosniff"
