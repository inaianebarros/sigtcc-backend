import logging
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass

from django.db import connection, transaction
from ninja.errors import HttpError

from apps.accounts.demo_seed import is_demo_account
from config.errors import ConflictError, FieldValidationError

from .rules import PersonaAction, PersonaRule

logger = logging.getLogger("sigtcc.personas")

ADVISORY_LOCK_KEY = 283_000_001
DOMAIN_ERRORS = (ConflictError, FieldValidationError, HttpError)


@dataclass
class CycleResult:
    actions: int = 0
    skipped: int = 0
    errors: int = 0

    def log(self) -> None:
        logger.info(
            "personas_cycle actions=%d skipped=%d errors=%d",
            self.actions,
            self.skipped,
            self.errors,
        )


@contextmanager
def instance_lock():
    """Yields whether this connection took the lock; it is held only for the cycle."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_KEY])
        acquired = cursor.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_KEY])


def run_cycle(rules: Iterable[PersonaRule], max_actions: int) -> CycleResult:
    result = CycleResult()
    for rule in rules:
        if result.actions >= max_actions:
            break
        try:
            for action in rule.pending_actions():
                if result.actions >= max_actions:
                    break
                _perform(action, result)
        except Exception:
            result.errors += 1
            logger.exception("personas rule %s failed while reading the state", rule.name)
    return result


def _perform(action: PersonaAction, result: CycleResult) -> None:
    if not is_demo_account(action.persona_email):
        result.skipped += 1
        logger.warning(
            "personas action %s skipped: %s is not a demo account",
            action.name,
            action.persona_email,
        )
        return
    try:
        with transaction.atomic():
            action.perform()
    except DOMAIN_ERRORS as error:
        result.skipped += 1
        logger.warning(
            "personas action %s of %s skipped: %r", action.name, action.persona_email, error
        )
    except Exception:
        result.errors += 1
        logger.exception("personas action %s of %s failed", action.name, action.persona_email)
    else:
        result.actions += 1
