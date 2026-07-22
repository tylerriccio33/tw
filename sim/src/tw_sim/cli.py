"""Headless all-AI simulation for balance watching:

    python -m tw_sim.cli --seed 42 --rounds 120

It is deliberately a thin script over the same `ai` / `turn` entry points the
renderer uses. Being able to write it in 60 lines against the public surface is
the engine-independence claim, tested.
"""

from __future__ import annotations

import argparse

from . import ai, data, event as ev
from .data import REBELS
from .turn import end_turn


def run(seed: int, rounds: int, quiet: bool = False) -> dict[str, int]:
    state = data.new_game(seed)
    state.factions[0].is_player = False  # nobody is watching; let the AI drive
    wars = battles = cities = 0

    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    while state.turn <= rounds and state.winner is None:
        events = ai.take_turn(state)
        events.extend(end_turn(state))
        for e in events:
            match e:
                case ev.WarDeclared():
                    wars += 1
                    say(
                        f"turn {state.turn:>3}: {state.factions[e.by].name} "
                        f"declares war on {state.factions[e.on].name}"
                    )
                case ev.FieldBattle() | ev.Assaulted():
                    battles += 1
                case ev.CityFell():
                    cities += 1
                    say(
                        f"turn {state.turn:>3}: {state.provinces[e.province].name} falls "
                        f"({state.factions[e.from_].name} -> {state.factions[e.to].name})"
                    )
                case ev.FactionDestroyed():
                    say(f"turn {state.turn:>3}: {state.factions[e.faction].name} DESTROYED")
                case ev.GameWon():
                    say(f"turn {state.turn:>3}: {state.factions[e.faction].name} WINS")

    say(f"\n=== after turn {min(state.turn, rounds)} (seed {seed}) ===")
    say(f"{wars} wars declared, {battles} battles/assaults, {cities} cities changed hands")
    standings = {}
    for f in state.living_factions():
        standings[state.factions[f].name] = len(state.faction_provinces(f))
        say(
            f"  {state.factions[f].name:<18} {len(state.faction_provinces(f))} provinces, "
            f"strength {state.strength(f):>3}, treasury {state.factions[f].treasury}g"
        )
    rebels = len(state.faction_provinces(REBELS))
    say(f"  rebels still hold {rebels} province(s)")

    return {
        "turn": min(state.turn, rounds),
        "wars": wars,
        "battles": battles,
        "cities": cities,
        "rebel_provinces": rebels,
        "winner": -1 if state.winner is None else state.winner,
        **{f"provinces:{k}": v for k, v in standings.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="tw_sim.cli", description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rounds", type=int, default=60)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(args.seed, args.rounds, args.quiet)


if __name__ == "__main__":
    main()
