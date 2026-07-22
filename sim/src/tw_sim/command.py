"""Everything a faction (human or AI) can ask the simulation to do on its turn.

Ending the turn is not a command: frontends call `turn.end_turn`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from . import wire
from .ids import ArmyId, FactionId, ProvinceId
from .state import Building, ProposalKind, UnitType


@dataclass(frozen=True, slots=True)
class Move:
    """Move to an adjacent province (1 movement point). Entering a province
    containing enemy field armies triggers a field battle."""

    kind: ClassVar[str] = "move"
    army: ArmyId
    to: ProvinceId


@dataclass(frozen=True, slots=True)
class Assault:
    """Storm the city in the army's current province. Walls favor the defender
    unless worn down by a siege. Consumes all movement."""

    kind: ClassVar[str] = "assault"
    army: ArmyId


@dataclass(frozen=True, slots=True)
class Besiege:
    """Settle in for a siege of the city here. Consumes all movement."""

    kind: ClassVar[str] = "besiege"
    army: ArmyId


@dataclass(frozen=True, slots=True)
class Recruit:
    """Queue one regiment of the given type for training in a city you own."""

    kind: ClassVar[str] = "recruit"
    province: ProvinceId
    unit: UnitType


@dataclass(frozen=True, slots=True)
class Build:
    """Start (or upgrade) a building in a city you own."""

    kind: ClassVar[str] = "build"
    province: ProvinceId
    building: Building


@dataclass(frozen=True, slots=True)
class RaiseArmy:
    """Pull regiments out of a garrison into a new field army. The strongest
    units muster first."""

    kind: ClassVar[str] = "raise_army"
    province: ProvinceId
    regiments: int


@dataclass(frozen=True, slots=True)
class Merge:
    """Combine two of your armies in the same province."""

    kind: ClassVar[str] = "merge"
    from_: ArmyId
    into: ArmyId


@dataclass(frozen=True, slots=True)
class Garrison:
    """Disband an army into the garrison of a city you own."""

    kind: ClassVar[str] = "garrison"
    army: ArmyId


@dataclass(frozen=True, slots=True)
class DeclareWar:
    kind: ClassVar[str] = "declare_war"
    on: FactionId


@dataclass(frozen=True, slots=True)
class Propose:
    kind: ClassVar[str] = "propose"
    to: FactionId
    #: Rust called this field `kind`; here `kind` is the union tag, so the
    #: treaty being offered is spelled `treaty` throughout.
    treaty: ProposalKind


@dataclass(frozen=True, slots=True)
class Respond:
    """Answer a proposal that was sent to you."""

    kind: ClassVar[str] = "respond"
    proposal: int
    accept: bool


Command = (
    Move
    | Assault
    | Besiege
    | Recruit
    | Build
    | RaiseArmy
    | Merge
    | Garrison
    | DeclareWar
    | Propose
    | Respond
)

COMMANDS = wire.registry(
    Move,
    Assault,
    Besiege,
    Recruit,
    Build,
    RaiseArmy,
    Merge,
    Garrison,
    DeclareWar,
    Propose,
    Respond,
)


def to_dict(cmd: Command) -> dict[str, Any]:
    return wire.to_dict(cmd)


def from_dict(raw: dict[str, Any]) -> Command:
    return wire.from_dict(COMMANDS, raw)
