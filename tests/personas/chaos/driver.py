"""One chaos run: a random real user and the persona cycles interleaved, checked at every step."""

import datetime
import logging
import random
import re
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from unittest import mock

from django.core.management import call_command
from django.db import transaction
from django.db.models import F

from apps.accounts.models import Teacher, User
from apps.activities.models import Activity, Review
from apps.advising.models import Advising, AdvisingRequest, AdvisingRequestStatus, AdvisingStatus
from apps.advising.services import request_advising
from apps.meetings.models import Meeting, MeetingStatus
from apps.personas import registry
from apps.personas.cycle import DOMAIN_ERRORS
from apps.personas.rules import PersonaAction, PersonaRule
from apps.personas.simulated_students import PROFILES
from apps.personas.simulated_teacher import SIMULATED_TEACHER_EMAIL
from tests.personas.chaos.invariants import InvariantMemory, all_violations
from tests.personas.chaos.real_users import RandomRealUser

CYCLE_LINE = re.compile(r"personas_cycle actions=(\d+) skipped=(\d+) errors=(\d+)")
CYCLE_WEIGHT = 25
TIME_WEIGHT = 12
INTERRUPTED_CYCLE_SHARE = 0.6
DISRUPTIVE_ACTIONS = (
    "answer_request",
    "review_activity",
    "change_status",
    "answer_meeting",
    "edit_meeting",
    "cancel_meeting",
    "delete_activity",
    "end_advising",
    "deactivate",
)
TRACE_SIZE = 25
SECONDS_THAT_MAKE_A_SIMULATED_ACCOUNT_ACT = (40, 130, 600)
UNANSWERED_AFTER = datetime.timedelta(days=8)
MEETING_MOVED_BACK = datetime.timedelta(days=3)


@dataclass
class Totals:
    persona_actions: int = 0
    persona_skipped: int = 0
    cycles: int = 0
    real_done: int = 0
    real_refused: int = 0


class ChaosFailure(AssertionError):
    pass


class InterruptedRule:
    """A rule that lets the real user act after the rule read the database and before the
    simulated account acts, so the state the account decided on may be gone when it acts."""

    def __init__(self, rule: PersonaRule, interrupt: Callable[[], None]) -> None:
        self.name = rule.name
        self._rule = rule
        self._interrupt = interrupt

    def pending_actions(self) -> Iterable[PersonaAction]:
        for action in self._rule.pending_actions():
            self._interrupt()
            yield action


class ChaosRun:
    def __init__(self, seed: int, steps: int, caplog, course, areas) -> None:
        self.seed = seed
        self.steps = steps
        self.caplog = caplog
        self.rng = random.Random(seed)
        self.real = RandomRealUser(self.rng, course, areas)
        self.memory = InvariantMemory()
        self.totals = Totals()
        self.trace: deque[str] = deque(maxlen=TRACE_SIZE)

    def execute(self) -> Totals:
        try:
            self._bootstrap()
            for step in range(self.steps):
                self._step(step)
        except BaseException as error:
            error.add_note(self._report())
            raise
        return self.totals

    def _report(self) -> str:
        return (
            f"chaos seed={self.seed} steps={self.steps}; reproduce with "
            f"CHAOS_SEED={self.seed} CHAOS_RUNS=1 CHAOS_STEPS={self.steps} pytest -m chaos\n"
            f"last steps:\n  " + "\n  ".join(self.trace)
        )

    def _bootstrap(self) -> None:
        teachers = [self.real.join_as_teacher() for _ in range(2)]
        students = [self.real.join_as_student() for _ in range(2)]
        self._cycle("bootstrap")
        assert Teacher.objects.filter(user__email__iexact=SIMULATED_TEACHER_EMAIL).exists()
        for teacher in teachers:
            assert teacher.advisings.filter(status=AdvisingStatus.ACTIVE).count() == len(PROFILES)
        request_advising(students[0], self._simulated_teacher_id())
        self._age_requests(SECONDS_THAT_MAKE_A_SIMULATED_ACCOUNT_ACT[1])
        self._cycle("bootstrap")
        assert students[0].advisings.filter(status=AdvisingStatus.ACTIVE).exists()

    def summary(self) -> str:
        """What the run reached, to show that the random user did exercise the rules."""
        counts = {
            "advisings": Counter(Advising.objects.values_list("status", flat=True)),
            "requests": Counter(AdvisingRequest.objects.values_list("status", flat=True)),
            "activities": Counter(Activity.objects.values_list("status", flat=True)),
            "reviews": Counter(Review.objects.values_list("decision", flat=True)),
            "meetings": Counter(Meeting.objects.values_list("status", flat=True)),
            "deactivated": Counter(
                User.objects.filter(is_active=False).values_list("type", flat=True)
            ),
        }
        return " ".join(f"{name}={dict(sorted(count.items()))}" for name, count in counts.items())

    def _simulated_teacher_id(self) -> int:
        return Teacher.objects.get(user__email__iexact=SIMULATED_TEACHER_EMAIL).user_id

    def _step(self, step: int) -> None:
        names = [*RandomRealUser.WEIGHTS, "cycle", "time"]
        weights = [*RandomRealUser.WEIGHTS.values(), CYCLE_WEIGHT, TIME_WEIGHT]
        name = self.rng.choices(names, weights)[0]
        label = f"{step}: {name}"
        if name == "cycle":
            self._cycle(label, interrupted=self.rng.random() < INTERRUPTED_CYCLE_SHARE)
        elif name == "time":
            self._advance_time(label)
        else:
            self._real_user_acts(label, name)
        self._assert_invariants(label)

    def _real_user_acts(self, label: str, name: str) -> None:
        attempt = self.real.plan(name)
        if attempt is None:
            self.trace.append(f"{label} (nothing to act on)")
            return
        repetitions = 2 if self.rng.random() < 0.25 else 1
        for _ in range(repetitions):
            try:
                with transaction.atomic():
                    attempt()
            except DOMAIN_ERRORS as error:
                self.totals.real_refused += 1
                self.trace.append(f"{label} refused: {error!r}")
            else:
                self.totals.real_done += 1
                self.trace.append(f"{label} done")

    def _interrupt(self, label: str) -> None:
        if self.rng.random() < 0.6:
            name = self.rng.choice(DISRUPTIVE_ACTIONS)
            self._real_user_acts(f"{label} interrupted by {name}", name)

    def _cycle(self, label: str, interrupted: bool = False) -> None:
        rules = registry.RULES
        if interrupted:
            rules = [InterruptedRule(rule, lambda: self._interrupt(label)) for rule in rules]
        self.caplog.clear()
        with (
            mock.patch.object(registry, "RULES", rules),
            self.caplog.at_level(logging.INFO, logger="sigtcc.personas"),
        ):
            call_command("run_personas", once=True)
        lines = [
            m for m in map(logging.LogRecord.getMessage, self.caplog.records) if CYCLE_LINE.match(m)
        ]
        assert len(lines) == 1, f"expected one cycle line, got {lines}"
        actions, skipped, errors = map(int, CYCLE_LINE.match(lines[0]).groups())
        self.trace.append(f"{label}: {lines[0]}")
        self.totals.cycles += 1
        self.totals.persona_actions += actions
        self.totals.persona_skipped += skipped
        if errors:
            raise ChaosFailure(
                f"the cycle ended with {errors} unexpected error(s):\n{self._errors()}"
            )

    def _errors(self) -> str:
        formatter = logging.Formatter()
        return "\n".join(
            formatter.format(record)
            for record in self.caplog.records
            if record.levelno >= logging.ERROR
        )

    def _age_requests(self, seconds: int) -> None:
        AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING).update(
            requested_at=F("requested_at") - datetime.timedelta(seconds=seconds)
        )

    def _advance_time(self, label: str) -> None:
        """Makes the delays of the simulated accounts pass, and sometimes the 7 days of RN20 and
        the time of a meeting, by moving the timestamps back."""
        seconds = self.rng.choice(SECONDS_THAT_MAKE_A_SIMULATED_ACCOUNT_ACT)
        delta = datetime.timedelta(seconds=seconds)
        self._age_requests(seconds)
        Activity.objects.update(created_at=F("created_at") - delta)
        Review.objects.update(decided_at=F("decided_at") - delta)
        moved = []
        if self.rng.random() < 0.15:
            AdvisingRequest.objects.filter(status=AdvisingRequestStatus.PENDING).update(
                requested_at=F("requested_at") - UNANSWERED_AFTER
            )
            moved.append("requests expired")
        if self.rng.random() < 0.25:
            meeting = (
                Meeting.objects.filter(
                    status__in=(MeetingStatus.PENDING, MeetingStatus.ACCEPTED),
                    held_at__isnull=True,
                )
                .order_by("pk")
                .first()
            )
            if meeting is not None:
                Meeting.objects.filter(pk=meeting.pk).update(
                    scheduled_at=F("scheduled_at") - MEETING_MOVED_BACK,
                    ends_at=F("ends_at") - MEETING_MOVED_BACK,
                )
                moved.append(f"meeting {meeting.pk} is past")
        self.trace.append(f"{label} {seconds}s {moved}")

    def _assert_invariants(self, label: str) -> None:
        violations = all_violations(self.memory, self.real.user_ids)
        if violations:
            raise ChaosFailure(f"after {label!r}:\n" + "\n".join(violations))
