import pytest

from apps.accounts.models import AreaOfInterest, Course, Institute

pytestmark = pytest.mark.django_db


def test_institutes_are_listed_without_authentication(client):
    Institute.objects.create(name="Instituto de Engenharia e Geociências")
    Institute.objects.create(name="Instituto de Ciências da Educação")

    response = client.get("/api/institutos")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == [
        "Instituto de Ciências da Educação",
        "Instituto de Engenharia e Geociências",
    ]


def test_courses_can_be_filtered_by_institute(client):
    ieg = Institute.objects.create(name="IEG")
    ice = Institute.objects.create(name="ICE")
    Course.objects.create(institute=ieg, name="Ciência da Computação")
    Course.objects.create(institute=ice, name="Pedagogia")

    response = client.get("/api/cursos", {"institute_id": ieg.id})

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Ciência da Computação"]
    assert response.json()[0]["institute_id"] == ieg.id


def test_all_courses_are_listed_when_no_institute_is_given(client):
    institute = Institute.objects.create(name="IEG")
    Course.objects.create(institute=institute, name="Ciência da Computação")
    Course.objects.create(institute=institute, name="Sistemas de Informação")

    response = client.get("/api/cursos")

    assert len(response.json()) == 2


def test_areas_of_interest_are_listed_alphabetically(client):
    AreaOfInterest.objects.create(name="Redes de Computadores")
    AreaOfInterest.objects.create(name="Banco de Dados")

    response = client.get("/api/areas-de-interesse")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == [
        "Banco de Dados",
        "Redes de Computadores",
    ]
