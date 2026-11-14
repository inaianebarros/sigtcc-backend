import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import UserType
from apps.activities.models import Activity, ActivityStatus
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import bearer
from tests.factories import make_advising, make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

URL = "/api/atividades"


def today():
    return timezone.localdate()


def days_from_today(days):
    return today() + datetime.timedelta(days=days)


def add_activity(
    advising,
    status=ActivityStatus.PENDING,
    deadline=None,
    title="Atividade",
    description="Descrição da atividade",
    **extra,
):
    return Activity.objects.create(
        advising=advising,
        title=title,
        description=description,
        deadline=deadline or days_from_today(7),
        status=status,
        **extra,
    )


def list_activities(client, user, **params):
    return client.get(URL, data=params, headers=bearer(user))


def advised_pair():
    course = make_course()
    teacher = make_teacher(courses=[course])
    student = make_student(course=course)
    return student, teacher, make_advising(student=student, teacher=teacher)


def ids_of(response):
    return [item["id"] for item in response.json()["items"]]


def test_the_student_lists_the_activities_of_their_active_advising(client):
    student, _, advising = advised_pair()
    activity = add_activity(advising, title="Capítulo 2", deadline=days_from_today(3))

    response = list_activities(client, student.user)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"] == [
        {
            "id": activity.id,
            "title": "Capítulo 2",
            "deadline": days_from_today(3).isoformat(),
            "status": "pending",
            "overdue": False,
            "student": {"id": student.user_id, "full_name": student.user.full_name},
        }
    ]


def end_advising(advising):
    Advising.objects.filter(pk=advising.pk).update(
        status=AdvisingStatus.ENDED, ended_at=timezone.now(), end_reason="Encerrada no teste."
    )


def test_a_student_sees_only_the_activities_of_their_own_active_advising(client):
    student, teacher, advising = advised_pair()
    mine = add_activity(advising)
    other_student = make_student(course=student.course)
    add_activity(make_advising(student=other_student, teacher=teacher))
    add_activity(make_advising(student=make_student(), teacher=make_teacher()))

    assert ids_of(list_activities(client, student.user)) == [mine.id]


def test_a_teacher_sees_the_activities_of_all_their_active_advisees(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    first = make_advising(student=make_student(course=course), teacher=teacher)
    second = make_advising(student=make_student(course=course), teacher=teacher)
    a, b = add_activity(first), add_activity(second)
    add_activity(make_advising(student=make_student(course=course), teacher=make_teacher()))

    response = list_activities(client, teacher.user)

    assert sorted(ids_of(response)) == sorted([a.id, b.id])
    students = {item["student"]["id"] for item in response.json()["items"]}
    assert students == {first.student.user_id, second.student.user_id}


def test_deleted_archived_and_ended_advising_activities_appear_for_nobody(client):
    student, teacher, advising = advised_pair()
    visible = add_activity(advising)
    add_activity(advising, deleted_at=timezone.now())
    add_activity(advising, status=ActivityStatus.ARCHIVED)
    ended_student = make_student(course=student.course)
    ended = make_advising(student=ended_student, teacher=teacher)
    add_activity(ended, status=ActivityStatus.COMPLETED)
    end_advising(ended)

    assert ids_of(list_activities(client, student.user)) == [visible.id]
    assert ids_of(list_activities(client, teacher.user)) == [visible.id]
    assert ids_of(list_activities(client, ended_student.user)) == []


def test_a_new_advising_of_the_student_does_not_inherit_the_old_activities(client):
    student, teacher, advising = advised_pair()
    add_activity(advising, status=ActivityStatus.COMPLETED)
    end_advising(advising)
    new_advising = make_advising(student=student, teacher=make_teacher(courses=[student.course]))
    fresh = add_activity(new_advising)

    assert ids_of(list_activities(client, student.user)) == [fresh.id]


def test_an_administrator_is_refused_and_anonymous_is_unauthorized(client):
    admin = make_user(UserType.ADMIN)

    assert list_activities(client, admin).status_code == 403
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize(
    "status",
    [
        ActivityStatus.PENDING,
        ActivityStatus.IN_PROGRESS,
        ActivityStatus.IN_REVIEW,
        ActivityStatus.COMPLETED,
    ],
)
def test_the_status_filter_keeps_only_that_status(client, status):
    student, _, advising = advised_pair()
    wanted = add_activity(advising, status=status)
    for other in ActivityStatus.values:
        if other != status:
            add_activity(advising, status=other)

    assert ids_of(list_activities(client, student.user, status=status.value)) == [wanted.id]


@pytest.mark.parametrize("value", ["archived", "pendente", "", "PENDING", "all"])
def test_an_invalid_status_is_refused(client, value):
    student, _, advising = advised_pair()
    add_activity(advising)

    assert list_activities(client, student.user, status=value).status_code == 422


def test_overdue_is_false_on_the_deadline_day_and_true_the_day_after(client):
    student, _, advising = advised_pair()
    on_the_day = add_activity(advising, deadline=today())
    day_after = add_activity(advising, deadline=days_from_today(-1))
    future = add_activity(advising, deadline=days_from_today(1))

    items = {
        item["id"]: item["overdue"]
        for item in list_activities(client, student.user).json()["items"]
    }

    assert items == {on_the_day.id: False, day_after.id: True, future.id: False}


@pytest.mark.parametrize("status", [ActivityStatus.COMPLETED, ActivityStatus.ARCHIVED])
def test_a_completed_or_archived_activity_is_never_overdue(client, status):
    student, _, advising = advised_pair()
    add_activity(advising, status=status, deadline=days_from_today(-30))

    response = list_activities(client, student.user, status="completed")
    everything = list_activities(client, student.user, overdue="true")

    assert all(item["overdue"] is False for item in response.json()["items"])
    assert ids_of(everything) == []


@pytest.mark.parametrize(
    "status",
    [ActivityStatus.PENDING, ActivityStatus.IN_PROGRESS, ActivityStatus.IN_REVIEW],
)
def test_an_open_activity_past_its_deadline_is_overdue_and_keeps_its_status(client, status):
    student, _, advising = advised_pair()
    activity = add_activity(advising, status=status, deadline=days_from_today(-2))

    item = list_activities(client, student.user).json()["items"][0]

    assert item["id"] == activity.id
    assert item["overdue"] is True
    assert item["status"] == status.value


def test_the_overdue_filter_keeps_the_late_ones_or_the_not_late_ones(client):
    student, _, advising = advised_pair()
    late = add_activity(advising, deadline=days_from_today(-1))
    on_time = add_activity(advising, deadline=today())
    done = add_activity(advising, status=ActivityStatus.COMPLETED, deadline=days_from_today(-9))

    assert ids_of(list_activities(client, student.user, overdue="true")) == [late.id]
    assert sorted(ids_of(list_activities(client, student.user, overdue="false"))) == sorted(
        [on_time.id, done.id]
    )
    assert list_activities(client, student.user, overdue="talvez").status_code == 422


def test_the_student_filter_works_for_the_teacher_inside_their_scope(client):
    student, teacher, advising = advised_pair()
    other = make_advising(student=make_student(course=student.course), teacher=teacher)
    mine, theirs = add_activity(advising), add_activity(other)
    outsider = make_advising(student=make_student(), teacher=make_teacher())
    add_activity(outsider)

    assert ids_of(list_activities(client, teacher.user, student_id=student.user_id)) == [mine.id]
    assert ids_of(list_activities(client, teacher.user, student_id=other.student.user_id)) == [
        theirs.id
    ]
    assert ids_of(list_activities(client, teacher.user, student_id=outsider.student.user_id)) == []
    assert list_activities(client, teacher.user, student_id="abc").status_code == 422


def test_a_student_filtering_by_another_student_gets_an_empty_page(client):
    student, teacher, advising = advised_pair()
    add_activity(advising)
    other = make_advising(student=make_student(course=student.course), teacher=teacher)

    own = list_activities(client, student.user, student_id=student.user_id)
    foreign = list_activities(client, student.user, student_id=other.student.user_id)

    assert own.json()["count"] == 1
    assert foreign.status_code == 200
    assert foreign.json() == {"items": [], "count": 0}


def test_the_filters_combine_with_and(client):
    student, teacher, advising = advised_pair()
    other = make_advising(student=make_student(course=student.course), teacher=teacher)
    target = add_activity(advising, status=ActivityStatus.IN_PROGRESS, deadline=days_from_today(-3))
    add_activity(advising, status=ActivityStatus.IN_PROGRESS, deadline=days_from_today(2))
    add_activity(advising, status=ActivityStatus.PENDING, deadline=days_from_today(-3))
    add_activity(other, status=ActivityStatus.IN_PROGRESS, deadline=days_from_today(-3))

    response = list_activities(
        client, teacher.user, status="in_progress", overdue="true", student_id=student.user_id
    )

    assert ids_of(response) == [target.id]


def test_the_order_is_open_by_nearest_deadline_then_completed_newest_first_then_id(client):
    student, _, advising = advised_pair()
    late = add_activity(advising, deadline=days_from_today(-5), title="atrasada")
    soon = add_activity(advising, deadline=days_from_today(1), title="em breve")
    tied_first = add_activity(advising, deadline=days_from_today(4), title="empate 1")
    tied_second = add_activity(advising, deadline=days_from_today(4), title="empate 2")
    far = add_activity(advising, deadline=days_from_today(30), title="distante")
    old_done = add_activity(
        advising, status=ActivityStatus.COMPLETED, deadline=days_from_today(-20), title="antiga"
    )
    new_done = add_activity(
        advising, status=ActivityStatus.COMPLETED, deadline=days_from_today(10), title="recente"
    )
    tied_done_first = add_activity(
        advising, status=ActivityStatus.COMPLETED, deadline=days_from_today(-8), title="c1"
    )
    tied_done_second = add_activity(
        advising, status=ActivityStatus.COMPLETED, deadline=days_from_today(-8), title="c2"
    )

    Activity.objects.filter(pk__in=[tied_first.pk, tied_done_first.pk]).update(title="editada")

    assert ids_of(list_activities(client, student.user)) == [
        late.id,
        soon.id,
        tied_first.id,
        tied_second.id,
        far.id,
        new_done.id,
        tied_done_first.id,
        tied_done_second.id,
        old_done.id,
    ]


def test_the_list_is_paginated(client):
    student, _, advising = advised_pair()
    created = [add_activity(advising, deadline=days_from_today(n)) for n in range(1, 26)]

    first = list_activities(client, student.user)
    second = list_activities(client, student.user, limit=20, offset=20)
    small = list_activities(client, student.user, limit=5, offset=10)

    assert first.json()["count"] == 25
    assert ids_of(first) == [a.id for a in created[:20]]
    assert ids_of(second) == [a.id for a in created[20:]]
    assert ids_of(small) == [a.id for a in created[10:15]]
    assert list_activities(client, student.user, limit=0).status_code == 422
    assert list_activities(client, student.user, limit=101).status_code == 422
    assert list_activities(client, student.user, offset=-1).status_code == 422


def count_queries(client, user):
    with CaptureQueriesContext(connection) as queries:
        response = list_activities(client, user, limit=100)
    assert response.status_code == 200
    return len(queries)


@pytest.mark.parametrize("role", ["student", "teacher"])
def test_the_number_of_queries_does_not_grow_with_the_number_of_activities(client, role):
    student, teacher, advising = advised_pair()
    add_activity(advising)
    user = student.user if role == "student" else teacher.user
    few = count_queries(client, user)

    for _ in range(4):
        other = make_advising(student=make_student(course=student.course), teacher=teacher)
        for number in range(6):
            add_activity(other, deadline=days_from_today(number - 3))
    add_activity(advising, deadline=days_from_today(-4))

    assert count_queries(client, user) == few


def test_the_openapi_documents_the_list_with_its_filters_and_errors(client):
    operation = client.get("/api/openapi.json").json()["paths"]["/api/atividades"]["get"]

    parameters = {parameter["name"] for parameter in operation["parameters"]}
    assert {"status", "overdue", "student_id", "search", "limit", "offset"} <= parameters
    assert {"200", "401", "403", "422"} <= set(operation["responses"])


def titles_of(response):
    return [item["title"] for item in response.json()["items"]]


def test_the_search_keeps_the_titles_that_contain_the_text_in_any_position(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Pesquisa de temas", deadline=days_from_today(1))
    add_activity(advising, title="Revisão da pesquisa", deadline=days_from_today(2))
    add_activity(advising, title="Capítulo 2", deadline=days_from_today(3))

    response = list_activities(client, student.user, search="pesquisa")

    assert response.status_code == 200
    assert titles_of(response) == ["Pesquisa de temas", "Revisão da pesquisa"]
    assert response.json()["count"] == 2


def test_the_search_ignores_case_and_accents_in_both_directions(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Revisão Bibliográfica", deadline=days_from_today(1))
    add_activity(advising, title="Conclusao do capitulo", deadline=days_from_today(2))

    assert titles_of(list_activities(client, student.user, search="REVISAO")) == [
        "Revisão Bibliográfica"
    ]
    assert titles_of(list_activities(client, student.user, search="bibliográfica")) == [
        "Revisão Bibliográfica"
    ]
    assert titles_of(list_activities(client, student.user, search="conclusão")) == [
        "Conclusao do capitulo"
    ]


def test_the_search_trims_the_ends_and_an_empty_text_means_no_search(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Capítulo 2", deadline=days_from_today(1))
    add_activity(advising, title="Capítulo 3", deadline=days_from_today(2))

    assert titles_of(list_activities(client, student.user, search="  capítulo 3  ")) == [
        "Capítulo 3"
    ]
    for blank in ("", "   "):
        assert list_activities(client, student.user, search=blank).json()["count"] == 2


def test_wildcard_characters_in_the_search_are_treated_literally(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Meta 100% concluída", deadline=days_from_today(1))
    add_activity(advising, title="Meta 100X concluída", deadline=days_from_today(2))
    add_activity(advising, title="Plano_final", deadline=days_from_today(3))
    add_activity(advising, title="Plano final", deadline=days_from_today(4))

    assert titles_of(list_activities(client, student.user, search="100%")) == [
        "Meta 100% concluída"
    ]
    assert titles_of(list_activities(client, student.user, search="o_f")) == ["Plano_final"]


def test_the_search_limit_is_the_length_of_a_title(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Capítulo 2")

    assert list_activities(client, student.user, search="a" * 200).status_code == 200
    too_long = list_activities(client, student.user, search="a" * 201)
    assert too_long.status_code == 422
    assert too_long.json()["detail"][0]["loc"][-1] == "search"


def test_the_search_limit_counts_the_text_without_the_spaces_of_the_ends(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Capítulo 2")

    padded = list_activities(client, student.user, search="  " + "a" * 200 + "  ")

    assert padded.status_code == 200
    assert padded.json() == {"items": [], "count": 0}


def test_a_null_character_in_the_search_is_not_a_server_error_and_matches_nothing(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Capítulo 2")

    response = list_activities(client, student.user, search="a\x00b")

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_the_search_combines_with_the_other_filters(client):
    course = make_course()
    teacher = make_teacher(courses=[course])
    ana = make_student(course=course)
    bruno = make_student(course=course)
    first = make_advising(student=ana, teacher=teacher)
    second = make_advising(student=bruno, teacher=teacher)
    add_activity(
        first, ActivityStatus.COMPLETED, title="Pesquisa da Ana", deadline=days_from_today(1)
    )
    add_activity(
        first, ActivityStatus.PENDING, title="Pesquisa pendente da Ana", deadline=days_from_today(2)
    )
    add_activity(
        second, ActivityStatus.COMPLETED, title="Pesquisa do Bruno", deadline=days_from_today(3)
    )
    add_activity(
        second, ActivityStatus.COMPLETED, title="Outro assunto", deadline=days_from_today(4)
    )

    only_ana_completed = list_activities(
        client, teacher.user, search="pesquisa", status="completed", student_id=ana.user_id
    )
    overdue_none = list_activities(client, teacher.user, search="pesquisa", overdue="true")

    assert titles_of(only_ana_completed) == ["Pesquisa da Ana"]
    assert overdue_none.json() == {"items": [], "count": 0}


def test_the_search_count_and_the_pages_follow_the_filter(client):
    student, _, advising = advised_pair()
    for number in range(25):
        add_activity(advising, title=f"Pesquisa {number:02d}", deadline=days_from_today(number))
    for number in range(5):
        add_activity(advising, title=f"Outra coisa {number}", deadline=days_from_today(40 + number))

    first = list_activities(client, student.user, search="pesquisa", limit=20).json()
    second = list_activities(client, student.user, search="pesquisa", limit=20, offset=20).json()

    assert (first["count"], len(first["items"])) == (25, 20)
    assert (second["count"], len(second["items"])) == (25, 5)
    assert all("Pesquisa" in item["title"] for item in first["items"] + second["items"])


def test_the_search_does_not_change_the_order_or_the_scope(client):
    student, _, advising = advised_pair()
    other_student, _, other_advising = advised_pair()
    late = add_activity(advising, title="Pesquisa B", deadline=days_from_today(9))
    soon = add_activity(advising, title="Pesquisa A", deadline=days_from_today(2))
    done = add_activity(
        advising, ActivityStatus.COMPLETED, title="Pesquisa C", deadline=days_from_today(1)
    )
    add_activity(other_advising, title="Pesquisa de outro aluno")
    add_activity(advising, ActivityStatus.ARCHIVED, title="Pesquisa arquivada")
    add_activity(advising, title="Pesquisa excluída", deleted_at=timezone.now())

    response = list_activities(client, student.user, search="pesquisa")

    assert ids_of(response) == [soon.id, late.id, done.id]


def test_the_search_does_not_look_at_the_description(client):
    student, _, advising = advised_pair()
    add_activity(advising, title="Capítulo 2", description="Falar de pesquisa de campo")

    assert list_activities(client, student.user, search="pesquisa").json()["count"] == 0
