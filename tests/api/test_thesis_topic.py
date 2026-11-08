import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.advising.models import ThesisTopic
from apps.advising.services import close_advising
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher

pytestmark = pytest.mark.django_db

TOPIC = "Gestão de TCC com Django e React"
STUDENT_URL = "/api/tcc/tema"


def put_topic(client, user, url, description=TOPIC):
    return client.put(
        url,
        data={"description": description},
        content_type="application/json",
        headers=bearer(user),
    )


def get_topic(client, user, url):
    return client.get(url, headers=bearer(user))


def advised_student():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def test_the_student_registers_the_topic_and_reads_it_back(client):
    student, _, advising = advised_student()

    created = put_topic(client, student.user, STUDENT_URL)

    assert created.status_code == 201
    assert set(created.json()) == {"description", "updated_at"}
    assert created.json()["description"] == TOPIC
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC
    read = get_topic(client, student.user, STUDENT_URL)
    assert read.status_code == 200
    assert read.json() == created.json()


def test_the_student_changes_the_topic_and_the_topic_is_replaced(client):
    student, _, advising = advised_student()
    put_topic(client, student.user, STUDENT_URL)

    changed = put_topic(client, student.user, STUDENT_URL, "Um tema novo para o meu TCC")

    assert changed.status_code == 200
    assert changed.json()["description"] == "Um tema novo para o meu TCC"
    assert ThesisTopic.objects.filter(advising=advising).count() == 1
    assert get_topic(client, student.user, STUDENT_URL).json() == changed.json()


def test_the_description_is_trimmed(client):
    student, _, advising = advised_student()

    response = put_topic(client, student.user, STUDENT_URL, f"   {TOPIC}\n ")

    assert response.status_code == 201
    assert response.json()["description"] == TOPIC
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC


@pytest.mark.parametrize(
    "description",
    ["", "         ", "curto", " " * 5 + "123456789" + " " * 5, "x" * 1001, " a" * 600],
)
def test_an_invalid_description_is_refused_and_changes_nothing(client, description):
    student, _, advising = advised_student()
    put_topic(client, student.user, STUDENT_URL)

    response = put_topic(client, student.user, STUDENT_URL, description)

    assert response.status_code == 422
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC


@pytest.mark.parametrize("description", ["1234567890", "x" * 1000])
def test_the_limits_of_the_description_are_accepted(client, description):
    student, _, _ = advised_student()

    assert put_topic(client, student.user, STUDENT_URL, description).status_code == 201


def test_a_student_without_an_active_advising_reads_nulls_and_cannot_write(client):
    student = make_student()

    read = get_topic(client, student.user, STUDENT_URL)
    written = put_topic(client, student.user, STUDENT_URL)

    assert read.status_code == 200
    assert read.json() == {"description": None, "updated_at": None}
    assert written.status_code == 409
    assert ThesisTopic.objects.count() == 0


def test_an_advising_without_a_topic_reads_nulls(client):
    student, _, _ = advised_student()

    response = get_topic(client, student.user, STUDENT_URL)

    assert response.status_code == 200
    assert response.json() == {"description": None, "updated_at": None}


def test_a_teacher_cannot_use_the_student_route_and_anonymous_is_refused(client):
    _, teacher, _ = advised_student()

    for call in (get_topic, put_topic):
        assert call(client, teacher.user, STUDENT_URL).status_code == 403
    assert client.get(STUDENT_URL).status_code == 401
    assert client.put(STUDENT_URL, data={}, content_type="application/json").status_code == 401


def teacher_url(student):
    return f"/api/tcc/orientandos/{student.user_id}/tema"


def test_the_active_advisor_registers_changes_and_reads_the_topic(client):
    student, teacher, advising = advised_student()
    url = teacher_url(student)

    created = put_topic(client, teacher.user, url)
    changed = put_topic(client, teacher.user, url, "Tema ajustado pelo orientador")

    assert created.status_code == 201
    assert changed.status_code == 200
    assert changed.json()["description"] == "Tema ajustado pelo orientador"
    assert ThesisTopic.objects.get(advising=advising).description == "Tema ajustado pelo orientador"
    assert get_topic(client, teacher.user, url).json() == changed.json()
    assert get_topic(client, student.user, STUDENT_URL).json() == changed.json()


def test_the_advisor_reads_nulls_when_the_advising_has_no_topic_yet(client):
    student, teacher, _ = advised_student()

    response = get_topic(client, teacher.user, teacher_url(student))

    assert response.status_code == 200
    assert response.json() == {"description": None, "updated_at": None}


def test_the_advisor_route_applies_the_same_limits_and_trimming(client):
    student, teacher, advising = advised_student()
    url = teacher_url(student)

    assert put_topic(client, teacher.user, url, "curto").status_code == 422
    assert put_topic(client, teacher.user, url, "x" * 1001).status_code == 422
    assert ThesisTopic.objects.count() == 0
    assert put_topic(client, teacher.user, url, f"  {TOPIC}  ").json()["description"] == TOPIC


def test_a_teacher_without_an_active_advising_with_the_student_gets_the_same_404(client):
    student, owner, advising = advised_student()
    put_topic(client, owner.user, teacher_url(student))
    other = make_teacher(courses=[student.course])
    unlinked_student = make_student(course=student.course)
    ended_student = make_student(course=student.course)
    ended = make_advising(student=ended_student, teacher=other)
    type(ended).objects.filter(pk=ended.pk).update(
        status="ended", ended_at=ended.started_at, end_reason="Encerrada no teste."
    )
    ThesisTopic.objects.create(advising=ended, description="Tema de orientação encerrada")

    responses = [
        get_topic(client, other.user, teacher_url(student)),
        put_topic(client, other.user, teacher_url(student)),
        get_topic(client, other.user, teacher_url(unlinked_student)),
        put_topic(client, other.user, teacher_url(unlinked_student)),
        get_topic(client, other.user, teacher_url(ended_student)),
        put_topic(client, other.user, teacher_url(ended_student)),
        get_topic(client, other.user, "/api/tcc/orientandos/999999/tema"),
    ]

    assert {response.status_code for response in responses} == {404}
    assert len({response.content for response in responses}) == 1
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC
    assert ThesisTopic.objects.get(advising=ended).description == "Tema de orientação encerrada"


def test_a_student_cannot_use_the_advisor_route_and_anonymous_is_refused(client):
    student, _, _ = advised_student()
    url = teacher_url(student)

    for call in (get_topic, put_topic):
        assert call(client, student.user, url).status_code == 403
    assert client.get(url).status_code == 401
    assert client.put(url, data={}, content_type="application/json").status_code == 401


def test_the_topic_is_frozen_when_the_advising_ends_and_a_new_advising_starts_without_one(client):
    student, teacher, advising = advised_student()
    put_topic(client, student.user, STUDENT_URL)
    close_advising(advising, "Concluímos o ciclo de orientação.")

    frozen = [
        put_topic(client, student.user, STUDENT_URL),
        put_topic(client, teacher.user, teacher_url(student)),
    ]

    assert frozen[0].status_code == 409
    assert frozen[1].status_code == 404
    assert get_topic(client, student.user, STUDENT_URL).json() == {
        "description": None,
        "updated_at": None,
    }
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC

    new_teacher = make_teacher(courses=[student.course])
    new_advising = make_advising(student=student, teacher=new_teacher)
    assert get_topic(client, student.user, STUDENT_URL).json()["description"] is None
    assert get_topic(client, new_teacher.user, teacher_url(student)).json()["description"] is None
    assert (
        put_topic(
            client, new_teacher.user, teacher_url(student), "Tema da nova orientação"
        ).status_code
        == 201
    )
    assert ThesisTopic.objects.get(advising=advising).description == TOPIC
    assert ThesisTopic.objects.get(advising=new_advising).description == "Tema da nova orientação"
    assert get_topic(client, teacher.user, teacher_url(student)).status_code == 404


def count_queries(client, user, url, write):
    with CaptureQueriesContext(connection) as queries:
        response = put_topic(client, user, url) if write else get_topic(client, user, url)
    assert response.status_code in (200, 201)
    return len(queries)


@pytest.mark.parametrize("write", [False, True])
def test_the_number_of_queries_is_the_same_with_or_without_data_around(client, write):
    student, teacher, _ = advised_student()
    put_topic(client, student.user, STUDENT_URL)
    baseline = [
        count_queries(client, student.user, STUDENT_URL, write),
        count_queries(client, teacher.user, teacher_url(student), write),
    ]
    for _ in range(8):
        other_student, other_teacher, _ = advised_student()
        put_topic(client, other_student.user, STUDENT_URL)
        put_topic(client, other_teacher.user, teacher_url(other_student), "Outro tema qualquer")

    assert [
        count_queries(client, student.user, STUDENT_URL, write),
        count_queries(client, teacher.user, teacher_url(student), write),
    ] == baseline


def test_the_openapi_documents_both_routes_and_their_errors(client):
    paths = client.get("/api/openapi.json").json()["paths"]

    student = paths["/api/tcc/tema"]
    advisor = paths["/api/tcc/orientandos/{student_id}/tema"]
    assert set(student) == set(advisor) == {"get", "put"}
    assert {"200", "201", "409", "422"} <= set(student["put"]["responses"])
    assert {"200", "201", "404", "422"} <= set(advisor["put"]["responses"])
    assert "404" in advisor["get"]["responses"]
