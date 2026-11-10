"""The administrator manages areas of interest through the Django Admin (RFADM04-06, RN18)."""

import pytest
from django.db.models.deletion import ProtectedError

from apps.accounts.models import AREA_IN_USE_MESSAGE, AreaOfInterest, UserType
from tests.factories import make_area, make_student, make_teacher, make_user

pytestmark = pytest.mark.django_db

AREAS = "/admin/accounts/areaofinterest/"
INTRUDER_AREA = "Invasão"


def delete_url(area):
    return f"{AREAS}{area.pk}/delete/"


def assert_the_admin_refuses_to_delete(admin_client, area, protected_count=1):
    page = admin_client.get(delete_url(area))
    assert page.status_code == 200
    assert len(page.context["protected"]) == protected_count
    response = admin_client.post(delete_url(area), {"post": "yes"})
    assert response.status_code == 200
    assert AreaOfInterest.objects.filter(pk=area.pk).exists()


def test_the_administrator_lists_the_areas(admin_client):
    area = make_area()

    response = admin_client.get(AREAS)

    assert response.status_code == 200
    assert area.name in response.content.decode()


def test_the_administrator_creates_an_area(admin_client):
    response = admin_client.post(f"{AREAS}add/", {"name": "Engenharia de Software"})

    assert response.status_code == 302
    assert AreaOfInterest.objects.filter(name="Engenharia de Software").exists()


def test_the_administrator_edits_an_area(admin_client):
    area = make_area()

    response = admin_client.post(f"{AREAS}{area.pk}/change/", {"name": "Nome Novo"})

    assert response.status_code == 302
    area.refresh_from_db()
    assert area.name == "Nome Novo"


def test_the_administrator_deletes_an_area_without_bonds(admin_client):
    area = make_area()

    response = admin_client.post(delete_url(area), {"post": "yes"})

    assert response.status_code == 302
    assert not AreaOfInterest.objects.filter(pk=area.pk).exists()


@pytest.mark.parametrize("make_linked", [make_student, make_teacher])
def test_an_area_with_a_student_or_a_teacher_cannot_be_deleted_and_keeps_the_link(
    admin_client, make_linked
):
    area = make_area()
    linked = make_linked(areas=[area])

    assert_the_admin_refuses_to_delete(admin_client, area)

    assert list(linked.areas_of_interest.all()) == [area]


def test_the_refusal_page_lists_every_student_and_teacher_of_the_area(admin_client):
    area = make_area()
    make_student(areas=[area])
    make_student(areas=[area])
    make_teacher(areas=[area])

    assert_the_admin_refuses_to_delete(admin_client, area, protected_count=3)


def test_the_bulk_delete_of_the_admin_refuses_the_whole_batch_when_one_area_is_in_use(
    admin_client,
):
    in_use = make_area()
    make_student(areas=[in_use])
    free = make_area()

    response = admin_client.post(
        AREAS,
        {"action": "delete_selected", "_selected_action": [in_use.pk, free.pk], "post": "yes"},
    )

    assert response.status_code == 200
    assert len(response.context["protected"]) == 1
    assert AreaOfInterest.objects.filter(pk__in=[in_use.pk, free.pk]).count() == 2


@pytest.mark.parametrize("make_linked", [make_student, make_teacher])
def test_the_model_refuses_to_delete_an_area_with_a_student_or_a_teacher(make_linked):
    area = make_area()
    make_linked(areas=[area])

    with pytest.raises(ProtectedError, match=AREA_IN_USE_MESSAGE):
        area.delete()
    with pytest.raises(ProtectedError, match=AREA_IN_USE_MESSAGE):
        AreaOfInterest.objects.filter(pk=area.pk).delete()

    assert AreaOfInterest.objects.filter(pk=area.pk).exists()


def test_the_model_deletes_areas_without_bonds_one_by_one_or_in_bulk():
    single, first, second = make_area(), make_area(), make_area()

    single.delete()
    AreaOfInterest.objects.filter(pk__in=[first.pk, second.pk]).delete()

    assert not AreaOfInterest.objects.filter(pk__in=[single.pk, first.pk, second.pk]).exists()


@pytest.mark.parametrize("user_type", [UserType.STUDENT, UserType.TEACHER])
def test_a_student_or_a_teacher_does_not_manage_areas(client, user_type):
    client.force_login(make_user(user_type))
    area = make_area()

    responses = [
        client.get(AREAS),
        client.post(f"{AREAS}add/", {"name": INTRUDER_AREA}),
        client.post(delete_url(area), {"post": "yes"}),
    ]

    for response in responses:
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]
    assert not AreaOfInterest.objects.filter(name=INTRUDER_AREA).exists()
    assert AreaOfInterest.objects.filter(pk=area.pk).exists()
