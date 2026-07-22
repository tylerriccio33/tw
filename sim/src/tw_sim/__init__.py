"""tw-sim: a pure, engine-independent Total War-style campaign state machine.

The simulation owns all game rules. Frontends send `Command`s via `rules.apply`,
advance time with `turn.end_turn`, and render from the returned `Event`s plus
read-only access to `GameState` — or, better, drive the whole thing through the
`Simulation` facade in `api`, which is the only surface a renderer should know.

No I/O happens here. Unlike the Rust engine this replaces, determinism is *not*
a guarantee: randomness runs through a plain `random.Random(seed)`, so the same
seed gives a plausible campaign of the same shape, not a byte-identical replay.
"""

from .api import Simulation, WorldSnapshot
from .command import Command
from .data import new_game
from .event import Event
from .ids import ArmyId, FactionId, ProvinceId
from .rules import Rule, RuleError, apply
from .state import (
    Army,
    ArmySize,
    Building,
    City,
    DiploStatus,
    Faction,
    Force,
    GameState,
    Proposal,
    ProposalKind,
    Province,
    SettlementTier,
    UnitType,
)
from .turn import end_turn

__all__ = [
    "Army",
    "ArmyId",
    "ArmySize",
    "Building",
    "City",
    "Command",
    "DiploStatus",
    "Event",
    "Faction",
    "FactionId",
    "Force",
    "GameState",
    "Proposal",
    "ProposalKind",
    "Province",
    "ProvinceId",
    "Rule",
    "RuleError",
    "SettlementTier",
    "Simulation",
    "UnitType",
    "WorldSnapshot",
    "apply",
    "end_turn",
    "new_game",
    "run_ai_turn",
]


def run_ai_turn(state: GameState) -> list[Event]:
    """Run the current (AI) faction's whole turn: decide commands, apply them,
    then end the turn. Returns everything that happened."""
    from . import ai, turn as _turn

    events = ai.take_turn(state)
    events.extend(_turn.end_turn(state))
    return events
