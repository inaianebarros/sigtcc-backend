import importlib
import logging
from dataclasses import dataclass, field

import pytest
from django.core.management import call_command
from django.db import connection
from ninja.errors import HttpError

from apps.accounts.models import Institute
from apps.personas import registry
from apps.personas.cycle import ADVISORY_LOCK_KEY
from apps.personas.rules import PersonaAction
from config import settings as project_settings
from config.errors import ConflictError, FieldValidationError

pytestmark = pytest.mark.django_db

DEMO_EMAIL = "professor.simulado@demo.ufopa.local"
REAL_EMAIL = "professor.real@ufopa.edu.br"
CYCLE_LOG = "personas_cycle actions=%d skipped=%d errors=%d"


@dataclass
class StubRule:
    name: str
    actions: list[PersonaAction] = field(default_factory=list)

    def pending_actions(self):
        yield from self.actions


class ExplodingRule:
    name = "exploding"

    def pending_actions(self):
        raise RuntimeError("boom")
        yield


@pytest.fixture(autouse=True)
def personas_enabled(settings):
    settings.PERSONAS_ENABLED = True
    settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 20


@pytest.fixture
def rules(monkeypatch):
    registered: list = []
    monkeypatch.setattr(registry, "RULES", registered)
    return registered


@pytest.fixture
def performed():
    return []


def action_for(performed, label, email=DEMO_EMAIL, error=None):
    def perform():
        if error is not None:
            raise error
        performed.append(label)

    return PersonaAction(persona_email=email, name=label, perform=perform)


def run_once(caplog):
    with caplog.at_level(logging.INFO, logger="sigtcc.personas"):
        call_command("run_personas", once=True)
    return [record.getMessage() for record in caplog.records if record.name == "sigtcc.personas"]


def cycle_line(messages):
    return next(message for message in messages if message.startswith("personas_cycle "))


def has_cycle_line(messages):
    return any(message.startswith("personas_cycle ") for message in messages)


def hold_lock_on_another_connection():
    other = connection.copy()
    cursor = other.cursor()
    cursor.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_KEY])
    return other, cursor.fetchone()[0]


def test_runs_once_and_logs_the_cycle_counters(rules, performed, caplog):
    rules.append(
        StubRule("stub", [action_for(performed, "first"), action_for(performed, "second")])
    )

    messages = run_once(caplog)

    assert performed == ["first", "second"]
    assert cycle_line(messages) == CYCLE_LOG % (2, 0, 0)


def test_logs_zero_counters_when_no_rule_is_registered(rules, caplog):
    assert cycle_line(run_once(caplog)) == CYCLE_LOG % (0, 0, 0)


def test_is_disabled_by_default_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("PERSONAS_ENABLED", raising=False)

    reloaded = importlib.reload(project_settings)

    assert reloaded.PERSONAS_ENABLED is False


def test_does_not_act_when_disabled(settings, rules, performed, caplog):
    settings.PERSONAS_ENABLED = False
    rules.append(StubRule("stub", [action_for(performed, "first")]))

    messages = run_once(caplog)

    assert performed == []
    assert not has_cycle_line(messages)


def test_exits_without_acting_when_another_connection_holds_the_lock(rules, performed, caplog):
    rules.append(StubRule("stub", [action_for(performed, "first")]))
    other, acquired = hold_lock_on_another_connection()
    try:
        assert acquired is True
        messages = run_once(caplog)
    finally:
        other.close()

    assert performed == []
    assert not has_cycle_line(messages)


def test_releases_the_lock_at_the_end_of_the_cycle(rules, performed, caplog):
    rules.append(StubRule("stub", [action_for(performed, "first")]))
    run_once(caplog)

    other, acquired = hold_lock_on_another_connection()
    other.close()

    assert acquired is True


@pytest.mark.parametrize(
    "error",
    [
        ConflictError("already answered"),
        FieldValidationError({"reason": "required"}),
        HttpError(404, "not found"),
    ],
)
def test_domain_error_is_logged_and_the_other_actions_still_run(rules, performed, caplog, error):
    rules.append(
        StubRule(
            "stub",
            [action_for(performed, "failing", error=error), action_for(performed, "next")],
        )
    )

    messages = run_once(caplog)

    assert performed == ["next"]
    assert cycle_line(messages) == CYCLE_LOG % (1, 1, 0)
    assert any("failing" in message and DEMO_EMAIL in message for message in messages)


def test_unexpected_error_is_logged_with_persona_and_action_and_the_cycle_goes_on(
    rules, performed, caplog
):
    rules.append(StubRule("first", [action_for(performed, "failing", error=RuntimeError("boom"))]))
    rules.append(StubRule("second", [action_for(performed, "next")]))

    messages = run_once(caplog)

    assert performed == ["next"]
    assert cycle_line(messages) == CYCLE_LOG % (1, 0, 1)
    assert any("failing" in message and DEMO_EMAIL in message for message in messages)


def test_a_rule_that_fails_while_reading_does_not_stop_the_next_rule(rules, performed, caplog):
    rules.append(ExplodingRule())
    rules.append(StubRule("stub", [action_for(performed, "next")]))

    messages = run_once(caplog)

    assert performed == ["next"]
    assert cycle_line(messages) == CYCLE_LOG % (1, 0, 1)


def test_does_not_act_on_accounts_outside_the_demo_domain(rules, performed, caplog):
    rules.append(
        StubRule(
            "stub",
            [
                action_for(performed, "real", email=REAL_EMAIL),
                action_for(performed, "lookalike", email="x@demo.ufopa.local.example.com"),
                action_for(performed, "demo"),
            ],
        )
    )

    messages = run_once(caplog)

    assert performed == ["demo"]
    assert cycle_line(messages) == CYCLE_LOG % (1, 2, 0)


def test_respects_the_action_limit_per_cycle(settings, rules, performed, caplog):
    settings.PERSONAS_MAX_ACTIONS_PER_CYCLE = 2
    rules.append(StubRule("one", [action_for(performed, f"a{n}") for n in range(3)]))
    rules.append(StubRule("two", [action_for(performed, "b0")]))

    messages = run_once(caplog)

    assert performed == ["a0", "a1"]
    assert cycle_line(messages) == CYCLE_LOG % (2, 0, 0)


def test_each_action_runs_in_its_own_transaction(rules, caplog):
    def create_then_fail():
        Institute.objects.create(name="rolled back")
        raise RuntimeError("boom")

    def create():
        Institute.objects.create(name="kept")

    rules.append(
        StubRule(
            "stub",
            [
                PersonaAction(DEMO_EMAIL, "failing", create_then_fail),
                PersonaAction(DEMO_EMAIL, "ok", create),
            ],
        )
    )

    run_once(caplog)

    names = Institute.objects.filter(name__in=["rolled back", "kept"]).values_list(
        "name", flat=True
    )
    assert list(names) == ["kept"]
