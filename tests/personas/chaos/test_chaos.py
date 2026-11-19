import os

import pytest

from tests.personas.chaos.driver import ChaosRun

pytestmark = [pytest.mark.chaos, pytest.mark.django_db]

FIRST_SEED = int(os.environ.get("CHAOS_SEED", "1"))
RUNS = int(os.environ.get("CHAOS_RUNS", "5"))
STEPS = int(os.environ.get("CHAOS_STEPS", "150"))
SEEDS = range(FIRST_SEED, FIRST_SEED + RUNS)


@pytest.mark.parametrize("seed", SEEDS, ids=lambda seed: f"seed={seed}")
def test_a_random_real_user_does_not_break_the_simulated_accounts(seed, caplog, course, areas):
    run = ChaosRun(seed, STEPS, caplog, course, areas)
    totals = run.execute()

    print(f"\nchaos seed={seed} steps={STEPS} {totals}\n  {run.summary()}")
    assert totals.persona_actions > 0
    assert totals.real_done > 0
