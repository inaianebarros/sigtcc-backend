"""Two real processes: while one holds the personas lock, a second `run_personas` does not act."""

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest
from django.db import connection

from apps.accounts.demo_seed import DEMO_DOMAIN
from apps.accounts.models import Teacher, User
from apps.advising.models import Advising
from apps.personas.simulated_students import PROFILES
from apps.personas.simulated_teacher import SIMULATED_TEACHER_EMAIL
from tests.factories import make_teacher

pytestmark = [pytest.mark.chaos, pytest.mark.django_db(transaction=True)]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROCESS_TIMEOUT_SECONDS = 60
BUSY_LOG = "another instance holds the lock"
CYCLE_LOG = "personas_cycle actions="


def _database_url() -> str:
    settings = connection.settings_dict
    credentials = f"{quote(settings['USER'], safe='')}:{quote(settings['PASSWORD'], safe='')}"
    return f"postgres://{credentials}@{settings['HOST']}:{settings['PORT']}/{settings['NAME']}"


@pytest.fixture
def process_env() -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": _database_url(),
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_LOG_LEVEL": "INFO",
        "PERSONAS_ENABLED": "true",
    }


@contextmanager
def lock_holder(env: dict[str, str]) -> Iterator[subprocess.Popen]:
    holder = subprocess.Popen(
        [sys.executable, "-m", "tests.personas.chaos.lock_holder"],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    watchdog = threading.Timer(PROCESS_TIMEOUT_SECONDS, holder.kill)
    watchdog.start()
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        yield holder
    finally:
        watchdog.cancel()
        holder.kill()
        holder.wait()


def run_personas_once(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "manage.py", "run_personas", "--once"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def simulated_accounts() -> int:
    return User.objects.filter(email__iendswith=f"@{DEMO_DOMAIN}").count()


@pytest.fixture
def real_teacher(course, areas):
    return make_teacher(courses=[course], areas=areas)


def test_the_second_instance_does_not_act_while_the_first_holds_the_lock(process_env, real_teacher):
    with lock_holder(process_env):
        blocked = run_personas_once(process_env)

        assert blocked.returncode == 0, blocked.stderr
        assert BUSY_LOG in blocked.stderr
        assert CYCLE_LOG not in blocked.stderr
        assert simulated_accounts() == 0


@pytest.mark.parametrize("how_the_holder_ends", ["releases", "is_killed"])
def test_the_next_instance_acts_once_the_lock_is_free(
    process_env, real_teacher, how_the_holder_ends
):
    with lock_holder(process_env) as holder:
        assert BUSY_LOG in run_personas_once(process_env).stderr
        if how_the_holder_ends == "releases":
            holder.stdin.write("\n")
            holder.stdin.flush()
        else:
            holder.kill()
        holder.wait(timeout=PROCESS_TIMEOUT_SECONDS)

        free = run_personas_once(process_env)

    assert free.returncode == 0, free.stderr
    assert CYCLE_LOG in free.stderr
    assert "errors=0" in free.stderr
    assert Teacher.objects.filter(user__email__iexact=SIMULATED_TEACHER_EMAIL).count() == 1
    assert Advising.objects.filter(teacher=real_teacher).count() == len(PROFILES)
