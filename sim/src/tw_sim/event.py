"""Everything the simulation reports back. Frontends render these; the
simulation never prints.

As with `command`, `kind` is the union tag, so the Rust `kind: ProposalKind`
field on the three proposal events is spelled `treaty` here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from . import wire
from .ids import ArmyId, FactionId, ProvinceId
from .state import Building, ProposalKind, UnitType


@dataclass(frozen=True, slots=True)
class TurnBegan:
    kind: ClassVar[str] = "turn_began"
    faction: FactionId
    turn: int
    income: int
    upkeep: int


@dataclass(frozen=True, slots=True)
class Desertion:
    """Bankrupt faction's army lost a regiment."""

    kind: ClassVar[str] = "desertion"
    army: ArmyId
    remaining: int


@dataclass(frozen=True, slots=True)
class Moved:
    kind: ClassVar[str] = "moved"
    army: ArmyId
    from_: ProvinceId
    to: ProvinceId


@dataclass(frozen=True, slots=True)
class FieldBattle:
    kind: ClassVar[str] = "field_battle"
    attacker: FactionId
    defender: FactionId
    province: ProvinceId
    attacker_regiments: int
    defender_regiments: int
    attacker_won: bool
    winner_losses: int


@dataclass(frozen=True, slots=True)
class Assaulted:
    kind: ClassVar[str] = "assaulted"
    attacker: FactionId
    province: ProvinceId
    attacker_regiments: int
    garrison: int
    attacker_won: bool
    winner_losses: int


@dataclass(frozen=True, slots=True)
class SiegeStarted:
    kind: ClassVar[str] = "siege_started"
    faction: FactionId
    province: ProvinceId


@dataclass(frozen=True, slots=True)
class SiegeLifted:
    kind: ClassVar[str] = "siege_lifted"
    province: ProvinceId


@dataclass(frozen=True, slots=True)
class CityFell:
    """Garrison starved out or stormed; city changed hands."""

    kind: ClassVar[str] = "city_fell"
    province: ProvinceId
    from_: FactionId
    to: FactionId


@dataclass(frozen=True, slots=True)
class RecruitQueued:
    kind: ClassVar[str] = "recruit_queued"
    province: ProvinceId
    unit: UnitType


@dataclass(frozen=True, slots=True)
class RegimentsTrained:
    kind: ClassVar[str] = "regiments_trained"
    province: ProvinceId
    count: int


@dataclass(frozen=True, slots=True)
class ConstructionStarted:
    kind: ClassVar[str] = "construction_started"
    province: ProvinceId
    building: Building


@dataclass(frozen=True, slots=True)
class ConstructionFinished:
    kind: ClassVar[str] = "construction_finished"
    province: ProvinceId
    building: Building


@dataclass(frozen=True, slots=True)
class ArmyRaised:
    kind: ClassVar[str] = "army_raised"
    army: ArmyId
    province: ProvinceId
    regiments: int


@dataclass(frozen=True, slots=True)
class ArmiesMerged:
    kind: ClassVar[str] = "armies_merged"
    from_: ArmyId
    into: ArmyId


@dataclass(frozen=True, slots=True)
class Garrisoned:
    kind: ClassVar[str] = "garrisoned"
    army: ArmyId
    province: ProvinceId


@dataclass(frozen=True, slots=True)
class WarDeclared:
    kind: ClassVar[str] = "war_declared"
    by: FactionId
    on: FactionId


@dataclass(frozen=True, slots=True)
class AllyJoinedWar:
    kind: ClassVar[str] = "ally_joined_war"
    ally: FactionId
    against: FactionId


@dataclass(frozen=True, slots=True)
class ProposalSent:
    kind: ClassVar[str] = "proposal_sent"
    id: int
    from_: FactionId
    to: FactionId
    treaty: ProposalKind


@dataclass(frozen=True, slots=True)
class ProposalAccepted:
    kind: ClassVar[str] = "proposal_accepted"
    from_: FactionId
    to: FactionId
    treaty: ProposalKind


@dataclass(frozen=True, slots=True)
class ProposalRejected:
    kind: ClassVar[str] = "proposal_rejected"
    from_: FactionId
    to: FactionId
    treaty: ProposalKind


@dataclass(frozen=True, slots=True)
class FactionDestroyed:
    kind: ClassVar[str] = "faction_destroyed"
    faction: FactionId


@dataclass(frozen=True, slots=True)
class GameWon:
    kind: ClassVar[str] = "game_won"
    faction: FactionId


Event = (
    TurnBegan
    | Desertion
    | Moved
    | FieldBattle
    | Assaulted
    | SiegeStarted
    | SiegeLifted
    | CityFell
    | RecruitQueued
    | RegimentsTrained
    | ConstructionStarted
    | ConstructionFinished
    | ArmyRaised
    | ArmiesMerged
    | Garrisoned
    | WarDeclared
    | AllyJoinedWar
    | ProposalSent
    | ProposalAccepted
    | ProposalRejected
    | FactionDestroyed
    | GameWon
)

EVENTS = wire.registry(
    TurnBegan,
    Desertion,
    Moved,
    FieldBattle,
    Assaulted,
    SiegeStarted,
    SiegeLifted,
    CityFell,
    RecruitQueued,
    RegimentsTrained,
    ConstructionStarted,
    ConstructionFinished,
    ArmyRaised,
    ArmiesMerged,
    Garrisoned,
    WarDeclared,
    AllyJoinedWar,
    ProposalSent,
    ProposalAccepted,
    ProposalRejected,
    FactionDestroyed,
    GameWon,
)


def to_dict(ev: Event) -> dict[str, Any]:
    return wire.to_dict(ev)


def from_dict(raw: dict[str, Any]) -> Event:
    return wire.from_dict(EVENTS, raw)
