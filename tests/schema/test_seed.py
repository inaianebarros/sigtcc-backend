import pytest
from django.conf import settings
from django.core.management import call_command

from apps.accounts.models import AreaOfInterest, Course, Institute

pytestmark = pytest.mark.django_db

SEED_FIXTURE = settings.BASE_DIR / "fixtures" / "seed.json"

EXPECTED_AREAS = {
    "Inteligência Artificial",
    "Aprendizado de Máquina",
    "Ciência de Dados",
    "Engenharia de Software",
    "Banco de Dados",
    "Redes de Computadores",
    "Segurança da Informação",
    "Sistemas Distribuídos",
    "Computação em Nuvem",
    "Desenvolvimento Web",
    "Computação Móvel",
    "Interação Humano-Computador",
    "Informática na Educação",
    "Computação Gráfica",
    "Processamento de Imagens",
    "Algoritmos e Estruturas de Dados",
    "Sistemas Embarcados e IoT",
}


def test_seed_loads_the_initial_catalog():
    call_command("loaddata", SEED_FIXTURE, verbosity=0)

    institute = Institute.objects.get(name="Instituto de Engenharia e Geociências")
    assert Course.objects.filter(institute=institute, name="Ciência da Computação").exists()
    assert set(AreaOfInterest.objects.values_list("name", flat=True)) == EXPECTED_AREAS


def test_seed_is_idempotent():
    call_command("loaddata", SEED_FIXTURE, verbosity=0)
    call_command("loaddata", SEED_FIXTURE, verbosity=0)

    assert Institute.objects.count() == 1
    assert Course.objects.count() == 1
    assert AreaOfInterest.objects.count() == len(EXPECTED_AREAS)
