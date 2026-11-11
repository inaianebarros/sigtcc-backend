"""The volume mode of the demonstration data: pagination, name search and history (#142)."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.demo_volume import VOLUME_ADVISINGS, VOLUME_PREFIX, VOLUME_TEACHERS
from apps.accounts.models import AreaOfInterest, Student, Teacher, User
from apps.advising.models import Advising, AdvisingStatus
from tests.api.helpers import LOGIN_URL, bearer, post_json

pytestmark = pytest.mark.django_db

FIRST_STUDENT = "aluno1.sem-orientacao"
FIRST_TEACHER = "prof1.orientandos"
SEARCH_URL = "/api/orientadores/busca"
HISTORY_URL = "/api/orientacao/historico"
PAGE_SIZE = 20
CAST_SIZE = 13


def user_of(slug):
    return User.objects.get(email=f"{slug}@{DEMO_DOMAIN}")


def seed(*options):
    out = StringIO()
    call_command("seed_demo_data", *options, stdout=out)
    return out.getvalue()


@pytest.fixture
def seeded_with_volume(catalog, debug):
    seed("--volume")


def search(client, **params):
    return client.get(SEARCH_URL, params, headers=bearer(user_of(FIRST_STUDENT)))


def history(client, **params):
    return client.get(HISTORY_URL, params, headers=bearer(user_of(FIRST_TEACHER)))


def volume_users():
    return User.objects.filter(email__startswith=VOLUME_PREFIX)


def test_without_the_option_nothing_extra_is_created(catalog, debug):
    seed()

    assert User.objects.count() == CAST_SIZE
    assert not volume_users().exists()


def test_with_the_option_it_adds_the_volume_teachers_and_the_ended_advisings(seeded_with_volume):
    assert Teacher.objects.filter(user__email__startswith=VOLUME_PREFIX).count() == VOLUME_TEACHERS
    assert Student.objects.filter(user__email__startswith=VOLUME_PREFIX).count() == VOLUME_ADVISINGS
    ended = Advising.objects.filter(
        teacher__user=user_of(FIRST_TEACHER), student__user__email__startswith=VOLUME_PREFIX
    )
    assert ended.count() == VOLUME_ADVISINGS
    assert set(ended.values_list("status", flat=True)) == {AdvisingStatus.ENDED}


def test_everything_added_is_identifiable_as_demonstration(seeded_with_volume):
    assert volume_users().count() == VOLUME_TEACHERS + VOLUME_ADVISINGS
    assert not volume_users().exclude(email__endswith=f"@{DEMO_DOMAIN}").exists()


def test_the_search_of_the_first_student_passes_more_than_one_page(client, seeded_with_volume):
    first = search(client).json()
    second = search(client, offset=PAGE_SIZE).json()

    assert first["count"] == VOLUME_TEACHERS + 2
    assert first["count"] > PAGE_SIZE
    assert len(first["items"]) == PAGE_SIZE
    assert len(second["items"]) > 0
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == len(set(ids))


def test_the_search_keeps_the_order_by_compatibility_across_the_pages(client, seeded_with_volume):
    items = []
    for offset in range(0, VOLUME_TEACHERS + 2, PAGE_SIZE):
        items += search(client, offset=offset).json()["items"]

    compatibilities = [item["compatibility"] for item in items]
    assert compatibilities == sorted(compatibilities, reverse=True)
    volume_ids = {t.user_id for t in Teacher.objects.filter(user__email__startswith=VOLUME_PREFIX)}
    assert {item["compatibility"] for item in items if item["id"] in volume_ids} == {1, 2, 3}


def test_the_name_search_ignores_accents_and_case(client, seeded_with_volume):
    with_accent = search(client, name="João").json()
    without_accent = search(client, name="joao").json()
    shouting = search(client, name="JOAO").json()

    assert with_accent["count"] > 0
    assert with_accent["count"] == without_accent["count"] == shouting["count"]
    assert 0 < without_accent["count"] < VOLUME_TEACHERS
    assert all("joão" in item["full_name"].lower() for item in without_accent["items"])


def test_the_history_of_the_first_teacher_passes_more_than_one_page(client, seeded_with_volume):
    first = history(client).json()
    second = history(client, offset=PAGE_SIZE).json()

    assert first["count"] == VOLUME_ADVISINGS + 1
    assert len(first["items"]) == PAGE_SIZE
    assert len(second["items"]) == PAGE_SIZE
    ended_at = [item["ended_at"] for item in first["items"] + second["items"]]
    assert ended_at == sorted(ended_at, reverse=True)
    assert len(set(ended_at)) == len(ended_at)


def test_a_volume_teacher_signs_in_with_the_single_password(browser, seeded_with_volume):
    volume_teacher = Teacher.objects.filter(user__email__startswith=VOLUME_PREFIX).first()

    response = post_json(
        browser,
        LOGIN_URL,
        {"email": volume_teacher.user.email, "password": "Senha-Demo-2026"},
    )

    assert response.status_code == 200


def test_running_it_again_with_the_option_duplicates_nothing(seeded_with_volume):
    counts = (User.objects.count(), Advising.objects.count(), Teacher.objects.count())

    seed("--volume")

    assert counts == (User.objects.count(), Advising.objects.count(), Teacher.objects.count())


def test_running_without_the_option_afterwards_keeps_what_was_added(seeded_with_volume):
    before = volume_users().count()

    seed()

    assert volume_users().count() == before


def test_the_volume_does_not_change_the_small_set(client, seeded_with_volume):
    for slug in ("aluno5.orientacao-completa", "aluno7.orientacao-encerrada"):
        assert Advising.objects.filter(student__user=user_of(slug)).count() == 1
    assert Advising.objects.filter(status=AdvisingStatus.ACTIVE).count() == 2


def test_the_option_also_needs_the_debug_mode(catalog, settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DJANGO_DEBUG"):
        call_command("seed_demo_data", "--volume", stdout=StringIO())

    assert User.objects.count() == 0


def test_the_output_tells_how_many_were_added(catalog, debug):
    first = seed("--volume")
    second = seed("--volume")

    assert str(VOLUME_TEACHERS) in first
    assert "nenhum dado novo" in second.lower()


def test_a_missing_area_of_the_catalog_stops_the_volume_with_a_clear_error(catalog, debug):
    AreaOfInterest.objects.filter(name="Computação Gráfica").delete()

    with pytest.raises(CommandError, match="áreas"):
        call_command("seed_demo_data", "--volume", stdout=StringIO())

    assert not volume_users().exists()
