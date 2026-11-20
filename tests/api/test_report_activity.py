"""An activity read from the report of an ended advising: detail, timeline and files (RF61, RN47)."""

import pytest

from apps.activities.models import ActivityStatus
from apps.advising.models import Advising
from apps.advising.services import close_advising
from tests.api.activity_flow import (
    accept,
    advised_pair,
    change_status,
    comment,
    create_activity,
    deliver,
    refuse,
)
from tests.api.helpers import bearer
from tests.api.report_builders import add_activity, ended_advising
from tests.api.uploads import PDF, upload
from tests.factories import make_admin, make_advising, make_student, make_teacher

pytestmark = pytest.mark.django_db


def base(advising_id, activity_id):
    return f"/api/orientacao/historico/{advising_id}/atividades/{activity_id}"


def get(client, user, url, **params):
    return client.get(url, params, headers=bearer(user))


def end(advising):
    close_advising(Advising.objects.get(pk=advising.pk), "Encerrada para o teste.")


def refused_activity(client):
    """An activity with a delivery, a refusal with a file and a comment, in an active advising."""
    student, teacher, advising = advised_pair()
    activity = create_activity(client, teacher, student)
    assert deliver(client, student, activity).status_code == 201
    assert change_status(client, student, activity, "in_review").status_code == 200
    response = refuse(client, teacher, activity, files=[upload("correcoes.pdf", PDF)])
    assert response.status_code == 200
    assert comment(client, student.user, activity).status_code == 201
    return student, teacher, advising, activity


def test_the_detail_after_the_end_is_the_detail_before_it_with_the_activity_archived(client):
    student, teacher, advising, activity = refused_activity(client)
    before = get(client, student.user, f"/api/atividades/{activity.id}").json()
    end(advising)

    response = get(client, student.user, base(advising.id, activity.id))

    assert response.status_code == 200
    assert response.json() == {**before, "status": "archived", "overdue": False}


def test_the_teacher_reads_a_completed_activity_of_the_advising_he_ended(client):
    student, teacher, advising = advised_pair()
    activity = create_activity(client, teacher, student)
    deliver(client, student, activity)
    change_status(client, student, activity, "in_review")
    assert accept(client, teacher, activity).status_code == 200
    end(advising)

    response = get(client, teacher.user, base(advising.id, activity.id))

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["teacher"]["full_name"] == teacher.user.full_name


@pytest.mark.parametrize("party", ["student", "teacher"])
def test_the_timeline_after_the_end_is_the_timeline_before_it(client, party):
    student, teacher, advising, activity = refused_activity(client)
    user = student.user if party == "student" else teacher.user
    before = get(client, user, f"/api/atividades/{activity.id}/linha-do-tempo", limit=2).json()
    end(advising)

    response = get(client, user, f"{base(advising.id, activity.id)}/linha-do-tempo", limit=2)

    assert response.status_code == 200
    assert response.json() == before
    older = get(
        client,
        user,
        f"{base(advising.id, activity.id)}/linha-do-tempo",
        before=before["items"][-1]["index"],
    )
    assert older.status_code == 200
    assert [item["index"] for item in older.json()["items"]] == list(
        range(before["items"][-1]["index"] - 1, 0, -1)
    )


@pytest.mark.parametrize("party", ["student", "teacher"])
def test_both_parties_download_the_delivery_and_the_refusal_files(client, party):
    student, teacher, advising, activity = refused_activity(client)
    detail = get(client, student.user, f"/api/atividades/{activity.id}").json()
    end(advising)
    user = student.user if party == "student" else teacher.user
    delivered = detail["submissions"][0]["files"][0]
    review = detail["reviews"][0]
    attached = review["files"][0]

    delivery = get(client, user, f"{base(advising.id, activity.id)}/arquivos/{delivered['id']}")
    refusal = get(
        client,
        user,
        f"{base(advising.id, activity.id)}/revisoes/{review['id']}/arquivos/{attached['id']}",
    )

    for response, name in ((delivery, "a.pdf"), (refusal, "correcoes.pdf")):
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PDF
        assert response["Content-Disposition"] == f'attachment; filename="{name}"'
        assert response["X-Content-Type-Options"] == "nosniff"


def test_a_file_of_another_activity_or_decision_is_not_found(client):
    student, teacher, advising, activity = refused_activity(client)
    other = create_activity(client, teacher, student)
    deliver(client, student, other)
    detail = get(client, student.user, f"/api/atividades/{activity.id}").json()
    other_file = get(client, student.user, f"/api/atividades/{other.id}").json()["submissions"][0][
        "files"
    ][0]
    review = detail["reviews"][0]
    attached = review["files"][0]
    end(advising)
    url = base(advising.id, activity.id)

    assert get(client, student.user, f"{url}/arquivos/{other_file['id']}").status_code == 404
    assert get(client, student.user, f"{url}/arquivos/{attached['id']}").status_code == 404
    wrong_review = f"{url}/revisoes/{review['id'] + 999}/arquivos/{attached['id']}"
    assert get(client, student.user, wrong_review).status_code == 404


def paths(advising_id, activity_id):
    url = base(advising_id, activity_id)
    return [url, f"{url}/linha-do-tempo", f"{url}/arquivos/1", f"{url}/revisoes/1/arquivos/1"]


@pytest.mark.parametrize("path", range(4))
def test_someone_who_is_not_a_party_gets_not_found(client, path):
    advising = ended_advising()
    activity = add_activity(advising)

    for outsider in (make_student().user, make_teacher().user):
        assert get(client, outsider, paths(advising.id, activity.id)[path]).status_code == 404


@pytest.mark.parametrize("path", range(4))
def test_an_active_advising_has_no_report_activity(client, path):
    advising = make_advising()
    activity = add_activity(advising, status=ActivityStatus.COMPLETED)

    response = get(client, advising.student.user, paths(advising.id, activity.id)[path])

    assert response.status_code == 404


@pytest.mark.parametrize("path", range(4))
def test_an_activity_of_another_advising_of_the_same_user_is_not_found(client, path):
    student = make_student()
    advising = ended_advising(student)
    other = add_activity(ended_advising(student))

    response = get(client, student.user, paths(advising.id, other.id)[path])

    assert response.status_code == 404


@pytest.mark.parametrize("path", range(4))
def test_a_deleted_activity_is_not_found(client, path):
    advising = ended_advising()
    activity = add_activity(advising, deleted=True)

    response = get(client, advising.student.user, paths(advising.id, activity.id)[path])

    assert response.status_code == 404


@pytest.mark.parametrize("path", range(4))
def test_a_visitor_is_unauthorized_and_the_administrator_is_forbidden(client, path):
    advising = ended_advising()
    url = paths(advising.id, add_activity(advising).id)[path]

    assert client.get(url).status_code == 401
    assert get(client, make_admin(), url).status_code == 403
