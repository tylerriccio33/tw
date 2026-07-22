"""No tactical battles: greater effective capability wins.

Capability is the gold-weighted strength of a force (cavalry count for more than
melee), scaled by a small random morale roll (90-110%) plus any defender bonus
(walls). The loser is wiped out; the winner loses, in *units*, half the loser's
head count rounded up — and those losses fall on its cheapest troops first (see
`Force.remove_units`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleResult:
    attacker_won: bool
    #: Units the winner lost.
    winner_losses: int


def decide(attacker_effective: int, defender_effective: int) -> bool:
    """Pure decision rule: ties favor the defender."""
    return attacker_effective > defender_effective


def resolve(
    rng: random.Random,
    attacker_capability: int,
    defender_capability: int,
    attacker_units: int,
    defender_units: int,
    defender_bonus_pct: int,
) -> BattleResult:
    """Decide a battle from each side's capability, then size the winner's
    losses off the loser's head count."""
    att_roll = rng.randint(90, 110)
    def_roll = rng.randint(90, 110)
    att_eff = attacker_capability * att_roll
    # All operands are non-negative, so floor division matches Rust's truncation.
    def_eff = defender_capability * def_roll * (100 + defender_bonus_pct) // 100
    attacker_won = decide(att_eff, def_eff)
    loser_units = defender_units if attacker_won else attacker_units
    return BattleResult(attacker_won, -(-loser_units // 2))
