"""The `Simulation` facade — the ONLY thing a frontend touches.

The whole point of the migration is that nothing above this line knows how the
rules work, and nothing below it knows there is a renderer. Unreal, a CLI, and
an AI-training loop all drive the same seven methods; the simulation cannot tell
them apart.

Note the granularity: `end_turn()` resolves the player's turn *and* every AI
faction's turn, in one call, and hands back the whole event stream. There is no
per-object accessor here on purpose — a frontend asking `army_position(42)`
sixty times a second is the design mistake this interface exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import ai, data, rules, turn
from .command import Command
from .event import Event
from .event import to_dict as event_to_dict
from .ids import ArmyId, FactionId, ProvinceId
from .state import GameState

#: Safety valve for `end_turn` on a campaign with no human faction: without a
#: player to hand control back to, the AI loop would never terminate.
_MAX_AI_TURNS = 64


@dataclass(frozen=True, slots=True)
class ProvinceView:
    id: ProvinceId
    name: str
    city: str
    tier: int
    owner: FactionId
    population: int
    adjacent: list[ProvinceId]
    garrison: list[int]
    walls: int
    farm: int
    market: int
    barracks: int
    recruit_queue: list[int]
    construction: list[int] | None
    besieged_by: FactionId | None

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__slots__}


@dataclass(frozen=True, slots=True)
class ArmyView:
    id: ArmyId
    owner: FactionId
    location: ProvinceId
    force: list[int]
    regiments: int
    mp: int
    general: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__slots__}


@dataclass(frozen=True, slots=True)
class FactionView:
    id: FactionId
    name: str
    treasury: int
    income: int
    upkeep: int
    is_player: bool
    is_rebel: bool
    alive: bool
    #: Status toward every other faction, indexed by FactionId.
    relations: list[int]
    opinions: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in self.__slots__}


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    """The full world, immutable, in one message. A few KB at this scale, which
    is why there is no diffing here — see the plan's sizing note."""

    turn: int
    current: FactionId
    winner: FactionId | None
    factions: list[FactionView]
    provinces: list[ProvinceView]
    armies: list[ArmyView]
    proposals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "current": self.current,
            "winner": self.winner,
            "factions": [f.to_dict() for f in self.factions],
            "provinces": [p.to_dict() for p in self.provinces],
            "armies": [a.to_dict() for a in self.armies],
            "proposals": self.proposals,
        }


def snapshot_of(state: GameState) -> WorldSnapshot:
    from . import economy

    n = len(state.factions)
    factions = [
        FactionView(
            id=FactionId(i),
            name=f.name,
            treasury=f.treasury,
            income=economy.faction_income(state, FactionId(i)),
            upkeep=economy.faction_upkeep(state, FactionId(i)),
            is_player=f.is_player,
            is_rebel=f.is_rebel,
            alive=f.alive,
            relations=[
                int(state.rel(FactionId(i), FactionId(j)).status) for j in range(n)
            ],
            opinions=[
                state.rel(FactionId(i), FactionId(j)).opinion for j in range(n)
            ],
        )
        for i, f in enumerate(state.factions)
    ]
    provinces = [
        ProvinceView(
            id=ProvinceId(i),
            name=p.name,
            city=p.city.name,
            tier=int(p.city.tier),
            owner=p.owner,
            population=p.city.population,
            adjacent=list(p.adjacent),
            garrison=[p.city.garrison.melee, p.city.garrison.archer, p.city.garrison.cav],
            walls=p.city.walls,
            farm=p.city.farm,
            market=p.city.market,
            barracks=p.city.barracks,
            recruit_queue=[
                p.city.recruit_queue.melee,
                p.city.recruit_queue.archer,
                p.city.recruit_queue.cav,
            ],
            construction=(
                None
                if p.city.construction is None
                else [int(p.city.construction.building), p.city.construction.turns_left]
            ),
            besieged_by=None if p.siege is None else p.siege.by,
        )
        for i, p in enumerate(state.provinces)
    ]
    armies = [
        ArmyView(
            id=id,
            owner=a.owner,
            location=a.location,
            force=[a.force.melee, a.force.archer, a.force.cav],
            regiments=a.force.total(),
            mp=a.mp,
            general=a.general,
            size=int(a.size()),
        )
        for id, a in state.armies.items()
    ]
    return WorldSnapshot(
        turn=state.turn,
        current=state.current,
        winner=state.winner,
        factions=factions,
        provinces=provinces,
        armies=armies,
        proposals=[
            {"id": p.id, "from": p.from_, "to": p.to, "treaty": int(p.kind)}
            for p in state.proposals
        ],
    )


class Simulation:
    def __init__(self, campaign: str = "britain", seed: int = 42) -> None:
        self.state = data.new_game(seed, campaign)
        self._events: list[Event] = []

    # -------- reads --------

    def snapshot(self) -> WorldSnapshot:
        """The full world state, as an immutable tree."""
        return snapshot_of(self.state)

    def is_player_turn(self) -> bool:
        return self.state.factions[self.state.current].is_player

    def winner(self) -> FactionId | None:
        return self.state.winner

    def consume_events(self) -> list[Event]:
        """Drain and clear the buffer of everything that has happened since the
        last drain."""
        out, self._events = self._events, []
        return out

    # -------- writes --------

    def apply(self, cmd: Command) -> list[Event]:
        """Apply one player command. Raises `RuleError` if it is illegal."""
        events = rules.apply(self.state, cmd)
        self._events.extend(events)
        return events

    def end_turn(self) -> list[Event]:
        """Resolve the current faction's turn, then run every AI faction until
        control comes back to the player. One call per button press."""
        events = turn.end_turn(self.state)
        for _ in range(_MAX_AI_TURNS):
            # Rechecked each pass: a player faction can be conquered mid-loop,
            # and then there is nobody left to hand control back to.
            has_player = any(f.is_player and f.alive for f in self.state.factions)
            if self.state.winner is not None:
                break
            if has_player and self.is_player_turn():
                break
            events.extend(ai.take_turn(self.state))
            events.extend(turn.end_turn(self.state))
            if not has_player:
                break
        self._events.extend(events)
        return events


def events_to_wire(events: list[Event]) -> list[dict[str, Any]]:
    return [event_to_dict(e) for e in events]
