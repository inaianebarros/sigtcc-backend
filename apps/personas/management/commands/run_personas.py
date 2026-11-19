import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.personas import registry
from apps.personas.cycle import instance_lock, run_cycle

logger = logging.getLogger("sigtcc.personas")

LOOP_INTERVAL_SECONDS = 15


class Command(BaseCommand):
    help = (
        "Runs the simulated accounts of the evaluation environment through the service layer "
        "(ADR 0022). Does nothing unless PERSONAS_ENABLED is true. A single instance acts at a "
        "time: a cycle that finds the lock taken exits without acting."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single cycle and exit instead of looping.",
        )

    def handle(self, *args, **options):
        if not settings.PERSONAS_ENABLED:
            logger.info("personas disabled: set PERSONAS_ENABLED=true to run the cycle")
            return
        while True:
            self._run_one_cycle()
            if options["once"]:
                return
            time.sleep(LOOP_INTERVAL_SECONDS)

    def _run_one_cycle(self) -> None:
        with instance_lock() as acquired:
            if not acquired:
                logger.info("personas cycle skipped: another instance holds the lock")
                return
            run_cycle(registry.RULES, settings.PERSONAS_MAX_ACTIONS_PER_CYCLE).log()
