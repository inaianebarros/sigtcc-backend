import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import ThesisProposal, UserType
from tests.api.helpers import bearer, post_json
from tests.factories import make_course, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

OWN = "/api/professores/me/propostas"


def proposal_url(proposal):
    return f"{OWN}/{proposal.id}"


def create(client, user, title):
    return post_json(client, OWN, {"title": title}, headers=bearer(user))


def edit(client, user, proposal, title):
    return client.patch(
        proposal_url(proposal),
        {"title": title},
        content_type="application/json",
        headers=bearer(user),
    )


def remove(client, user, proposal):
    return client.delete(proposal_url(proposal), headers=bearer(user))


def proposals_of(client, user, **params):
    return client.get(OWN, params, headers=bearer(user))


def make_proposal(teacher, title, days_ago=0):
    proposal = ThesisProposal.objects.create(teacher=teacher, title=title)
    ThesisProposal.objects.filter(pk=proposal.pk).update(
        created_at=timezone.now() - datetime.timedelta(days=days_ago)
    )
    return proposal


def titles(response):
    return [item["title"] for item in response.json()["items"]]


def test_the_teacher_creates_a_proposal_and_it_appears_in_the_list(client):
    teacher = make_teacher()

    response = create(client, teacher.user, "  Gestão de TCC com Django  ")

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Gestão de TCC com Django"
    assert set(body) == {"id", "title", "created_at"}
    assert ThesisProposal.objects.get(pk=body["id"]).teacher == teacher
    assert titles(proposals_of(client, teacher.user)) == ["Gestão de TCC com Django"]


@pytest.mark.parametrize("title", ["", "    ", "abcd", "  abcd  ", "a" * 151])
def test_a_title_outside_5_to_150_characters_is_refused(client, title):
    teacher = make_teacher()

    response = create(client, teacher.user, title)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "title"
    assert ThesisProposal.objects.count() == 0


def test_the_limits_of_the_title_are_accepted_without_counting_the_ends(client):
    teacher = make_teacher()

    shortest = create(client, teacher.user, "  abcde  ")
    longest = create(client, teacher.user, " " + "b" * 150 + " ")

    assert shortest.status_code == 201 and shortest.json()["title"] == "abcde"
    assert longest.status_code == 201 and len(longest.json()["title"]) == 150


def test_the_title_is_required(client):
    teacher = make_teacher()

    response = post_json(client, OWN, {}, headers=bearer(teacher.user))

    assert response.status_code == 422


def test_the_51st_proposal_is_refused_with_a_clear_message(client):
    teacher = make_teacher()
    for number in range(50):
        make_proposal(teacher, f"Proposta número {number:02d}")

    response = create(client, teacher.user, "Proposta que passa do limite")

    assert response.status_code == 409
    assert "50" in response.json()["detail"]
    assert ThesisProposal.objects.filter(teacher=teacher).count() == 50


def test_another_teachers_proposals_do_not_count_toward_the_limit(client):
    teacher = make_teacher()
    other = make_teacher()
    for number in range(50):
        make_proposal(other, f"Proposta número {number:02d}")

    assert create(client, teacher.user, "Minha primeira proposta").status_code == 201


def test_the_same_title_is_refused_ignoring_case_accents_and_the_ends(client):
    teacher = make_teacher()
    make_proposal(teacher, "Análise de Dados Educacionais")

    for repeated in (
        "análise de dados educacionais",
        "ANALISE DE DADOS EDUCACIONAIS",
        "  Analise  de dados educacionais ",
    ):
        response = create(client, teacher.user, repeated)
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"][-1] == "title"
        assert response.json()["detail"][0]["msg"] == "Você já tem uma proposta com esse título."
    assert ThesisProposal.objects.filter(teacher=teacher).count() == 1


def test_different_teachers_may_have_the_same_title(client):
    first, second = make_teacher(), make_teacher()
    create(client, first.user, "Ensino de programação")

    assert create(client, second.user, "Ensino de programação").status_code == 201


def test_the_teacher_edits_the_title_and_cannot_repeat_another_one(client):
    teacher = make_teacher()
    first = make_proposal(teacher, "Primeiro título de proposta")
    make_proposal(teacher, "Segundo título de proposta")

    renamed = edit(client, teacher.user, first, "  Título renovado da proposta ")
    repeated = edit(client, teacher.user, first, "segundo título de PROPOSTA")
    same = edit(client, teacher.user, first, "Título renovado da proposta")

    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Título renovado da proposta"
    assert repeated.status_code == 422
    assert same.status_code == 200
    first.refresh_from_db()
    assert first.title == "Título renovado da proposta"


def test_an_edit_with_an_invalid_title_is_refused(client):
    teacher = make_teacher()
    proposal = make_proposal(teacher, "Título válido da proposta")

    assert edit(client, teacher.user, proposal, "abc").status_code == 422
    assert edit(client, teacher.user, proposal, "x" * 151).status_code == 422
    assert ThesisProposal.objects.get(pk=proposal.pk).title == "Título válido da proposta"


def test_the_removal_is_definitive(client):
    teacher = make_teacher()
    proposal = make_proposal(teacher, "Proposta que será removida")

    response = remove(client, teacher.user, proposal)

    assert response.status_code == 204
    assert not ThesisProposal.objects.filter(pk=proposal.pk).exists()
    assert proposals_of(client, teacher.user).json() == {"items": [], "count": 0}
    assert create(client, teacher.user, "Proposta que será removida").status_code == 201


def test_a_teacher_cannot_edit_or_remove_the_proposal_of_another(client):
    owner, intruder = make_teacher(), make_teacher()
    proposal = make_proposal(owner, "Proposta do outro professor")

    edited = edit(client, intruder.user, proposal, "Título trocado pelo intruso")
    removed = remove(client, intruder.user, proposal)
    missing = client.delete(f"{OWN}/999999", headers=bearer(intruder.user))

    assert edited.status_code == removed.status_code == missing.status_code == 404
    assert edited.json() == removed.json() == missing.json()
    assert ThesisProposal.objects.get(pk=proposal.pk).title == "Proposta do outro professor"


def test_the_list_is_newest_first_then_by_id_with_pages_of_five_and_a_total(client):
    teacher = make_teacher()
    oldest = make_proposal(teacher, "Mais antiga de todas", days_ago=9)
    for number in range(6):
        make_proposal(teacher, f"Proposta intermediária {number}", days_ago=5 - number)
    tie_a = make_proposal(teacher, "Empate com o mesmo instante A", days_ago=0)
    tie_b = make_proposal(teacher, "Empate com o mesmo instante B", days_ago=0)
    ThesisProposal.objects.filter(pk__in=[tie_a.pk, tie_b.pk]).update(
        created_at=timezone.now() + datetime.timedelta(days=1)
    )

    first = proposals_of(client, teacher.user).json()
    second = proposals_of(client, teacher.user, offset=5).json()
    last = proposals_of(client, teacher.user, offset=5, limit=5).json()

    assert first["count"] == 9
    assert [item["id"] for item in first["items"][:2]] == [tie_b.id, tie_a.id]
    assert len(first["items"]) == 5 and len(second["items"]) == 4
    assert second["items"][-1]["id"] == oldest.id
    assert last == second
    seen = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(seen)) == 9


def test_the_page_size_never_exceeds_five(client):
    teacher = make_teacher()
    for number in range(8):
        make_proposal(teacher, f"Proposta número {number}")

    assert len(proposals_of(client, teacher.user).json()["items"]) == 5
    assert proposals_of(client, teacher.user, limit=6).status_code == 422
    assert proposals_of(client, teacher.user, limit=0).status_code == 422
    assert proposals_of(client, teacher.user, offset=-1).status_code == 422


def test_a_teacher_without_proposals_gets_an_empty_list_with_total_zero(client):
    teacher = make_teacher()

    assert proposals_of(client, teacher.user).json() == {"items": [], "count": 0}


def test_the_teacher_only_lists_their_own_proposals(client):
    teacher, other = make_teacher(), make_teacher()
    make_proposal(teacher, "Minha proposta de tema")
    make_proposal(other, "Proposta de outro professor")

    assert titles(proposals_of(client, teacher.user)) == ["Minha proposta de tema"]


def test_the_student_lists_the_proposals_of_a_teacher_of_their_course(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course], is_available=False)
    make_proposal(teacher, "Proposta mais antiga", days_ago=3)
    make_proposal(teacher, "Proposta mais recente", days_ago=1)

    response = client.get(
        f"/api/professores/{teacher.user_id}/propostas", headers=bearer(student.user)
    )

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert titles(response) == ["Proposta mais recente", "Proposta mais antiga"]


def test_the_student_gets_an_empty_list_for_a_teacher_without_proposals(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course])

    response = client.get(
        f"/api/professores/{teacher.user_id}/propostas", headers=bearer(student.user)
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


def test_other_courses_inactive_teachers_and_unknown_ids_get_the_same_404(client):
    course = make_course()
    student = make_student(course=course)
    elsewhere = make_teacher(courses=[make_course()])
    inactive = make_teacher(courses=[course], is_active=False)
    make_proposal(elsewhere, "Proposta de outro curso")
    make_proposal(inactive, "Proposta de professor inativo")

    responses = [
        client.get(f"/api/professores/{teacher_id}/propostas", headers=bearer(student.user))
        for teacher_id in (elsewhere.user_id, inactive.user_id, 999999)
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    assert responses[0].json() == responses[1].json() == responses[2].json()


def test_the_student_cannot_create_edit_or_remove(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course])
    proposal = make_proposal(teacher, "Proposta do professor")

    assert create(client, student.user, "Tentativa do aluno").status_code == 403
    assert edit(client, student.user, proposal, "Título do aluno aqui").status_code == 403
    assert remove(client, student.user, proposal).status_code == 403
    assert proposals_of(client, student.user).status_code == 403
    assert ThesisProposal.objects.get(pk=proposal.pk).title == "Proposta do professor"


def test_a_teacher_cannot_use_the_student_listing_and_visitors_are_not_authenticated(client):
    teacher = make_teacher()
    url = f"/api/professores/{teacher.user_id}/propostas"

    assert client.get(url, headers=bearer(teacher.user)).status_code == 403
    assert client.get(url, headers=bearer(make_user(UserType.ADMIN))).status_code == 403
    assert client.get(url).status_code == 401
    assert client.get(OWN).status_code == 401
    assert post_json(client, OWN, {"title": "Sem login aqui"}).status_code == 401


def test_the_profile_patch_neither_accepts_nor_returns_the_proposals(client):
    teacher = make_teacher()
    make_proposal(teacher, "Proposta já existente")

    patched = client.patch(
        "/api/professores/me/perfil",
        {"is_available": False, "thesis_proposals": [{"title": "Invasora do perfil"}]},
        content_type="application/json",
        headers=bearer(teacher.user),
    )
    profile = client.get("/api/professores/me/perfil", headers=bearer(teacher.user))

    assert patched.status_code in (200, 422)
    assert "thesis_proposals" not in patched.json()
    assert "thesis_proposals" not in profile.json()
    assert list(ThesisProposal.objects.values_list("title", flat=True)) == ["Proposta já existente"]


def test_the_proposals_do_not_appear_in_the_advisor_search_or_profile(client):
    course = make_course()
    student = make_student(course=course)
    teacher = make_teacher(courses=[course])
    make_proposal(teacher, "Proposta fora da busca")

    search = client.get(
        "/api/orientadores/busca", {"show_all": "true"}, headers=bearer(student.user)
    )
    profile = client.get(f"/api/professores/{teacher.user_id}/perfil", headers=bearer(student.user))

    assert "thesis_proposals" not in search.json()["items"][0]
    assert "thesis_proposals" not in profile.json()


def test_the_number_of_queries_does_not_depend_on_how_many_proposals_exist(client):
    teacher = make_teacher()
    make_proposal(teacher, "Primeira proposta de tema")

    def count():
        with CaptureQueriesContext(connection) as queries:
            assert proposals_of(client, teacher.user).status_code == 200
        return len(queries)

    few = count()
    for number in range(20):
        make_proposal(teacher, f"Mais uma proposta {number:02d}")

    assert count() == few


def test_the_openapi_documents_the_endpoints_and_the_pagination(client):
    schema = client.get("/api/openapi.json").json()
    paths = schema["paths"]

    own = paths["/api/professores/me/propostas"]
    assert {"get", "post"} <= set(own)
    assert {"limit", "offset"} <= {p["name"] for p in own["get"]["parameters"]}
    assert "5" in own["get"]["description"]
    assert {"patch", "delete"} <= set(paths["/api/professores/me/propostas/{proposal_id}"])
    student = paths["/api/professores/{teacher_id}/propostas"]["get"]
    assert "RN42" in student["description"]
    limit = next(p for p in own["get"]["parameters"] if p["name"] == "limit")
    assert limit["schema"]["maximum"] == 5 and limit["schema"]["default"] == 5


def test_saving_only_the_title_keeps_the_uniqueness_key_in_sync():
    teacher = make_teacher()
    proposal = make_proposal(teacher, "Título antes da troca")

    proposal.title = "Título Depois da Troca"
    proposal.save(update_fields=["title"])

    proposal.refresh_from_db()
    assert proposal.title_key == "titulo depois da troca"
