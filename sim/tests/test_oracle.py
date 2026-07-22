"""Aggregate-outcome checks against the retired Rust engine's observed behavior.

Determinism was de-scoped, so there was never an exact state to match:
`random.Random` will not reproduce ChaCha8's stream, and we do not want it to.
What *was* comparable is the shape of a campaign. These bounds were recorded
while the Rust engine still existed, running it over the seeds below for 120
rounds; it consistently produced:

  - the rebels wiped off the map inside 120 rounds, every seed;
  - England the strongest survivor, usually the outright winner;
  - a couple of dozen battles and 7-44 cities changing hands;
  - nobody deadlocked or crashed.

A campaign where the rebels never lose ground, or where England is bankrupt by
turn 10, is a bug even though no single assertion about exact state could catch
it. These are the bounds that would have caught one.

The engine is gone now, so these numbers can no longer be re-derived — they are
a frozen baseline. Widen them only with a deliberate balance change in hand,
never to make a red test go green.
"""

from __future__ import annotations

import pytest

from tw_sim.cli import run

#: The five seeds spot-checked against the Rust engine while both existed.
ORACLE_SEEDS = [1, 7, 42, 123, 999]


@pytest.mark.parametrize("seed", ORACLE_SEEDS)
def test_campaign_has_the_shape_the_rust_engine_produced(seed):
    r = run(seed, rounds=120, quiet=True)

    # The isles get carved up: rebels are always finished inside 120 rounds.
    assert r["rebel_provinces"] == 0, "rebels never lost a province"
    # War actually happens, and it changes the map.
    assert r["wars"] >= 1
    assert r["battles"] >= 10
    assert r["cities"] >= 5
    # England — the strongest opening position — should be doing well, not
    # collapsing. Every Rust run left it with the most land or an outright win.
    assert r.get("provinces:England", 0) >= 5 or r["winner"] == 0


@pytest.mark.parametrize("seed", range(20))
def test_twenty_seeds_run_to_completion_without_crashing_or_stalling(seed):
    """The plan's blunt requirement: run 20 seeds, none may crash or deadlock.

    `run` is bounded by `rounds`, so a stall would show up as the campaign
    burning all 120 rounds having done nothing at all — asserted here as
    'something was fought over'.
    """
    r = run(seed, rounds=120, quiet=True)
    assert r["turn"] >= 1
    assert r["battles"] > 0, "120 rounds and not one battle: the AI is stuck"


def test_early_turns_leave_england_solvent():
    """Bankruptcy by turn 10 would mean the economy port is wrong."""
    from tw_sim import new_game, end_turn, ai
    from tw_sim.data import ENGLAND

    state = new_game(42)
    state.factions[0].is_player = False
    while state.turn <= 10 and state.winner is None:
        ai.take_turn(state)
        end_turn(state)
    assert state.factions[ENGLAND].alive
    assert state.factions[ENGLAND].treasury > 0
