import pytest
from django.utils import timezone

from apps.accounts.models import PublishedArticle
from tests.api.helpers import bearer, errors_by_field
from tests.factories import make_area, make_student, make_teacher

pytestmark = pytest.mark.django_db

PROFILE_URL = "/api/alunos/me/perfil"


def test_a_student_reads_their_own_profile(client):
    student = make_student(registration_number="202411001")
    area = make_area()
    student.areas_of_interest.add(area)
    PublishedArticle.objects.create(
        student=student, title="Grafos em redes", year=2024, url="https://example.com/grafos"
    )

    response = client.get(PROFILE_URL, headers=bearer(student.user))

    assert response.status_code == 200
    assert response.json() == {
        "full_name": student.user.full_name,
        "email": student.user.email,
        "registration_number": "202411001",
        "institute_id": student.course.institute_id,
        "course_id": student.course_id,
        "area_ids": [area.id],
        "articles": [
            {"title": "Grafos em redes", "year": 2024, "url": "https://example.com/grafos"}
        ],
    }


def patch(client, user, payload):
    return client.patch(
        PROFILE_URL, data=payload, content_type="application/json", headers=bearer(user)
    )


def test_a_student_changes_only_the_name_and_leaves_the_rest_untouched(client):
    student = make_student()
    area = make_area()
    student.areas_of_interest.add(area)
    PublishedArticle.objects.create(student=student, title="Artigo", year=2023)

    response = patch(client, student.user, {"full_name": "  Ana Souza Lima  "})

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ana Souza Lima"
    assert body["area_ids"] == [area.id]
    assert [article["title"] for article in body["articles"]] == ["Artigo"]
    me = client.get("/api/auth/me", headers=bearer(student.user))
    assert me.json()["full_name"] == "Ana Souza Lima"


def test_the_areas_sent_replace_the_whole_list(client):
    student = make_student()
    kept, dropped, added = make_area(), make_area(), make_area()
    student.areas_of_interest.add(kept, dropped)

    response = patch(client, student.user, {"area_ids": [kept.id, added.id]})

    assert response.status_code == 200
    assert response.json()["area_ids"] == sorted([kept.id, added.id])
    assert set(student.areas_of_interest.values_list("id", flat=True)) == {kept.id, added.id}


@pytest.mark.parametrize(
    ("area_ids", "message"),
    [
        ([], "Escolha ao menos uma área de interesse."),
        ([999999], "Área de interesse não encontrada."),
    ],
)
def test_the_areas_cannot_be_emptied_or_unknown(client, area_ids, message):
    student = make_student()
    area = make_area()
    student.areas_of_interest.add(area)

    response = patch(client, student.user, {"area_ids": area_ids})

    assert response.status_code == 422
    assert errors_by_field(response) == {"area_ids": message}
    assert list(student.areas_of_interest.values_list("id", flat=True)) == [area.id]


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("   ", "Informe o nome completo."),
        ("A" * 151, "O nome deve ter no máximo 150 caracteres."),
    ],
)
def test_the_name_is_required_and_limited(client, name, message):
    student = make_student()
    original = student.user.full_name

    response = patch(client, student.user, {"full_name": name})

    assert response.status_code == 422
    assert errors_by_field(response) == {"full_name": message}
    student.user.refresh_from_db()
    assert student.user.full_name == original


def article(**overrides):
    return {"title": "Um artigo", "year": 2024, "url": "https://example.com/a", **overrides}


def test_the_articles_sent_replace_the_whole_list(client):
    student = make_student()
    PublishedArticle.objects.create(student=student, title="Antigo", year=2020)
    teacher_article = PublishedArticle.objects.create(
        teacher=make_teacher(), title="De outro dono", year=2021
    )

    response = patch(
        client,
        student.user,
        {"articles": [article(title="Novo"), article(title="Sem link", url="")]},
    )

    assert response.status_code == 200
    assert [(a["title"], a["url"]) for a in response.json()["articles"]] == [
        ("Novo", "https://example.com/a"),
        ("Sem link", ""),
    ]
    assert set(student.articles.values_list("title", flat=True)) == {"Novo", "Sem link"}
    assert PublishedArticle.objects.filter(pk=teacher_article.pk).exists()


def test_an_empty_list_removes_all_the_articles(client):
    student = make_student()
    PublishedArticle.objects.create(student=student, title="Antigo", year=2020)

    response = patch(client, student.user, {"articles": []})

    assert response.status_code == 200
    assert response.json()["articles"] == []
    assert not student.articles.exists()


@pytest.mark.parametrize(
    ("articles", "message"),
    [
        ([article(title="   ")], "Informe o título de cada artigo."),
        ([article(title="T" * 256)], "O título do artigo deve ter no máximo 255 caracteres."),
        ([article(year=1899)], "O ano do artigo deve estar entre 1900 e o ano atual."),
        (
            [article(year=timezone.localdate().year + 1)],
            "O ano do artigo deve estar entre 1900 e o ano atual.",
        ),
        (
            [article(url="ftp://example.com/a")],
            "A URL do artigo deve começar com http:// ou https:// e ter no máximo 500 caracteres.",
        ),
        (
            [article(url="https://example.com/" + "a" * 500)],
            "A URL do artigo deve começar com http:// ou https:// e ter no máximo 500 caracteres.",
        ),
        ([article() for _ in range(51)], "Informe no máximo 50 artigos."),
    ],
)
def test_the_articles_are_validated_and_nothing_changes_on_error(client, articles, message):
    student = make_student()
    PublishedArticle.objects.create(student=student, title="Antigo", year=2020)

    response = patch(client, student.user, {"articles": articles})

    assert response.status_code == 422
    assert errors_by_field(response) == {"articles": message}
    assert list(student.articles.values_list("title", flat=True)) == ["Antigo"]


def test_fifty_articles_are_accepted(client):
    student = make_student()

    response = patch(client, student.user, {"articles": [article() for _ in range(50)]})

    assert response.status_code == 200
    assert student.articles.count() == 50


@pytest.mark.parametrize(
    "field", ["email", "registration_number", "institute_id", "course_id", "password"]
)
def test_read_only_fields_are_rejected_and_nothing_changes(client, field):
    student = make_student()
    original_name = student.user.full_name

    response = patch(client, student.user, {"full_name": "Outro Nome", field: "x"})

    assert response.status_code == 422
    assert errors_by_field(response) == {field: "Este campo não pode ser alterado."}
    student.user.refresh_from_db()
    assert student.user.full_name == original_name


def test_an_unknown_field_is_rejected(client):
    student = make_student()

    response = patch(client, student.user, {"apelido": "Aninha"})

    assert response.status_code == 422
    assert errors_by_field(response) == {"apelido": "Campo não reconhecido."}


@pytest.mark.parametrize("method", ["get", "patch"])
def test_an_anonymous_visitor_is_not_authenticated(client, method):
    response = getattr(client, method)(PROFILE_URL, content_type="application/json")

    assert response.status_code == 401


@pytest.mark.parametrize("method", ["get", "patch"])
def test_a_teacher_cannot_use_the_student_profile(client, method):
    teacher = make_teacher()

    response = getattr(client, method)(
        PROFILE_URL, data={}, content_type="application/json", headers=bearer(teacher.user)
    )

    assert response.status_code == 403
