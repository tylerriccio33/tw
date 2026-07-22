"""`GameState` and every domain type the rules operate on."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import IntEnum

from .config import balance
from .ids import ArmyId, FactionId, ProvinceId


class UnitType(IntEnum):
    """The three kinds of regiment a settlement can raise. Ordered weakest to
    strongest by cost: casualties are taken from the cheapest units first, so
    elite cavalry outlive the levy."""

    MELEE = 0
    ARCHER = 1
    CAV = 2

    @property
    def label(self) -> str:
        return ("melee", "archer", "cavalry")[self]

    @property
    def cost(self) -> int:
        """Gold to recruit one, from `balance.yaml`. Cavalry costs the most,
        then archers, then melee."""
        b = balance()
        return (b.melee_cost, b.archer_cost, b.cav_cost)[self]

    @property
    def capability(self) -> int:
        """A unit's battlefield capability *is* the gold spent on it: a more
        expensive unit is exactly that much more capable."""
        return max(self.cost, 0)


#: Weakest first — the order losses are removed and the recruit queue is drained in.
UNIT_TYPES: tuple[UnitType, ...] = (UnitType.MELEE, UnitType.ARCHER, UnitType.CAV)

_FIELDS: tuple[str, ...] = ("melee", "archer", "cav")


@dataclass(slots=True)
class Force:
    """A body of troops, counted by type. Used both for field armies and for the
    garrison sitting inside a city. The engine decides battles by `capability`
    (gold-weighted), but sizes and upkeep still count heads via `total`.

    Unlike the Rust `Force` this is a mutable reference type, so anywhere the
    original code relied on `Copy` you want `.copy()`.
    """

    melee: int = 0
    archer: int = 0
    cav: int = 0

    @staticmethod
    def of_melee(n: int) -> Force:
        """A force of `n` basic melee units — how founding armies and starting
        garrisons (authored as a plain head count) are raised."""
        return Force(melee=n)

    def copy(self) -> Force:
        return Force(self.melee, self.archer, self.cav)

    def count(self, t: UnitType) -> int:
        return getattr(self, _FIELDS[t])

    def add(self, t: UnitType, n: int) -> None:
        setattr(self, _FIELDS[t], self.count(t) + n)

    def remove_units_of(self, t: UnitType, n: int) -> None:
        """Remove `n` units of a specific type (saturating)."""
        setattr(self, _FIELDS[t], max(0, self.count(t) - n))

    def total(self) -> int:
        """Head count across all types."""
        return self.melee + self.archer + self.cav

    def is_empty(self) -> bool:
        return self.total() == 0

    def capability(self) -> int:
        """Summed gold-weighted capability — the number that wins battles."""
        return sum(self.count(t) * t.capability for t in UNIT_TYPES)

    def merge(self, other: Force) -> None:
        """Fold another force into this one."""
        for t in UNIT_TYPES:
            self.add(t, other.count(t))

    def remove_units(self, n: int) -> int:
        """Remove up to `n` units, weakest (cheapest) first, so cavalry are the
        last to fall. Returns how many were actually removed."""
        removed = 0
        for t in UNIT_TYPES:
            cut = min(n, self.count(t))
            setattr(self, _FIELDS[t], self.count(t) - cut)
            n -= cut
            removed += cut
        return removed

    def split_off(self, n: int) -> Force:
        """Split `n` units off into a new force, taking the strongest first so a
        field army musters your best troops and leaves the levy behind."""
        out = Force()
        for t in reversed(UNIT_TYPES):
            take = min(n, self.count(t))
            setattr(self, _FIELDS[t], self.count(t) - take)
            out.add(t, take)
            n -= take
        return out


class Building(IntEnum):
    FARM = 0
    MARKET = 1
    BARRACKS = 2
    WALLS = 3

    @property
    def label(self) -> str:
        return ("farm", "market", "barracks", "walls")[self]


@dataclass(slots=True)
class Construction:
    building: Building
    turns_left: int


class SettlementTier(IntEnum):
    """How much of a place a settlement is. Ordered, so `>=` comparisons work.

    Fixed at map load for now; the point of naming the concept is that growth
    mechanics later have somewhere to promote a settlement *to*."""

    VILLAGE = 0
    TOWN = 1
    CITY = 2
    CAPITAL = 3

    @property
    def label(self) -> str:
        return ("village", "town", "city", "capital")[self]

    @property
    def base_population(self) -> int:
        """The population a settlement of this tier holds at map load. Bigger
        tiers house more people, and taxation scales with that head count, so a
        capital is worth far more to its owner than a village."""
        return (500, 2_000, 6_000, 12_000)[self]


@dataclass(slots=True)
class City:
    """The settlement at the heart of a province: what it's called, how big it
    is, what it's built, and what it's building."""

    #: The settlement's own name, which is not always the province's — the
    #: province of Scotland is held from Edinburgh.
    name: str
    tier: SettlementTier
    #: How many people live here. Seeded from the tier at map load and the base
    #: on which taxation — the crown's income — is levied.
    population: int
    garrison: Force
    walls: int
    farm: int
    market: int
    barracks: int
    #: Regiments queued for training, by type; a population-scaled number
    #: complete each turn (see `turn.py`).
    recruit_queue: Force = field(default_factory=Force)
    construction: Construction | None = None


@dataclass(slots=True)
class Siege:
    by: FactionId
    turns: int


@dataclass(slots=True)
class Province:
    name: str
    owner: FactionId
    adjacent: list[ProvinceId]
    base_income: int
    city: City
    siege: Siege | None = None


@dataclass(slots=True)
class Faction:
    name: str
    treasury: int
    is_player: bool
    is_rebel: bool
    alive: bool = True


class ArmySize(IntEnum):
    WARBAND = 0
    HOST = 1
    HORDE = 2


@dataclass(slots=True)
class Army:
    """A field army: who owns it, where it is, how strong it is, and who leads it."""

    owner: FactionId
    location: ProvinceId
    force: Force
    mp: int
    #: The general in command. Cosmetic today — traits and loyalty hang off this
    #: later. Assigned by `data.general_for`, never by the RNG.
    general: str

    def copy(self) -> Army:
        """A snapshot detached from the live army — the equivalent of the Rust
        `*state.armies.get(&id)`, which callers rely on to compare before/after."""
        return Army(self.owner, self.location, self.force.copy(), self.mp, self.general)

    def size(self) -> ArmySize:
        """Field armies read as a horde, a host, or a warband; frontends size
        their markers by this rather than reinventing the thresholds."""
        n = self.force.total()
        if n <= 3:
            return ArmySize.WARBAND
        if n <= 9:
            return ArmySize.HOST
        return ArmySize.HORDE


class DiploStatus(IntEnum):
    PEACE = 0
    WAR = 1
    TRADE = 2
    ALLIANCE = 3

    @property
    def label(self) -> str:
        return ("peace", "war", "trade", "alliance")[self]


@dataclass(slots=True)
class Relation:
    status: DiploStatus = DiploStatus.PEACE
    opinion: int = 0


class ProposalKind(IntEnum):
    PEACE = 0
    TRADE = 1
    ALLIANCE = 2

    @property
    def label(self) -> str:
        return ("peace", "trade agreement", "alliance")[self]


@dataclass(slots=True)
class Proposal:
    id: int
    from_: FactionId
    to: FactionId
    kind: ProposalKind


#: What `rel` reports for a pair with no stored relation — including a faction
#: with itself, which the n*n Rust matrix had a diagonal slot for and nothing
#: ever read.
_NEUTRAL = Relation()


@dataclass(slots=True)
class GameState:
    #: Campaign turn (a full round of all factions).
    turn: int
    #: Faction whose turn it currently is.
    current: FactionId
    factions: list[Faction]
    provinces: list[Province]
    armies: dict[ArmyId, Army]
    #: Diplomatic relations, keyed by unordered faction pair. The Rust engine
    #: kept a full n*n matrix and wrote both `[i][j]` and `[j][i]` on every
    #: mutation; keying on a frozenset makes that symmetry structural instead of
    #: a discipline.
    relations: dict[frozenset[FactionId], Relation]
    #: Proposals awaiting a human decision (AI recipients answer instantly).
    proposals: list[Proposal]
    next_army: int
    next_proposal: int
    rng: random.Random
    winner: FactionId | None = None

    # -------- diplomacy accessors --------

    def rel(self, a: FactionId, b: FactionId) -> Relation:
        if a == b:
            return _NEUTRAL
        return self.relations.setdefault(frozenset((a, b)), Relation())

    def set_status(self, a: FactionId, b: FactionId, status: DiploStatus) -> None:
        self.rel(a, b).status = status

    def shift_opinion(self, a: FactionId, b: FactionId, delta: int) -> None:
        r = self.rel(a, b)
        r.opinion = max(-100, min(100, r.opinion + delta))

    def at_war(self, a: FactionId, b: FactionId) -> bool:
        return a != b and self.rel(a, b).status == DiploStatus.WAR

    def allied(self, a: FactionId, b: FactionId) -> bool:
        return a != b and self.rel(a, b).status == DiploStatus.ALLIANCE

    def trading(self, a: FactionId, b: FactionId) -> bool:
        """Trade income flows for both trade agreements and alliances."""
        return a != b and self.rel(a, b).status in (
            DiploStatus.TRADE,
            DiploStatus.ALLIANCE,
        )

    # -------- queries --------

    def faction_provinces(self, f: FactionId) -> list[ProvinceId]:
        return [ProvinceId(i) for i, p in enumerate(self.provinces) if p.owner == f]

    def faction_armies(self, f: FactionId) -> list[ArmyId]:
        return [id for id, a in self.armies.items() if a.owner == f]

    def armies_in(self, p: ProvinceId) -> list[ArmyId]:
        return [id for id, a in self.armies.items() if a.location == p]

    def enemy_capability_in(self, f: FactionId, p: ProvinceId) -> int:
        """Summed enemy (at war with `f`) capability in field armies at `p`."""
        return sum(
            a.force.capability()
            for a in self.armies.values()
            if a.location == p and self.at_war(f, a.owner)
        )

    def strength(self, f: FactionId) -> int:
        """Rough military strength (gold-weighted capability) used by the AI and
        diplomacy thresholds. Field armies count double — they can strike."""
        field_ = sum(
            a.force.capability() for a in self.armies.values() if a.owner == f
        )
        garrison = sum(
            p.city.garrison.capability() for p in self.provinces if p.owner == f
        )
        return field_ * 2 + garrison

    def share_border(self, a: FactionId, b: FactionId) -> bool:
        """Do factions `a` and `b` own at least one pair of adjacent provinces?"""
        return any(
            p.owner == a and any(self.provinces[q].owner == b for q in p.adjacent)
            for p in self.provinces
        )

    def living_factions(self) -> list[FactionId]:
        """Living non-rebel factions."""
        return [
            FactionId(i)
            for i, f in enumerate(self.factions)
            if f.alive and not f.is_rebel
        ]

    def find_province(self, name: str) -> ProvinceId | None:
        return _find(name, [p.name for p in self.provinces], ProvinceId)

    def find_faction(self, name: str) -> FactionId | None:
        return _find(name, [f.name for f in self.factions], FactionId)


def _find(name: str, names: list[str], wrap):
    """Exact (case-insensitive) match wins, then a *unique* prefix."""
    lower = name.lower()
    for i, n in enumerate(names):
        if n.lower() == lower:
            return wrap(i)
    matches = [i for i, n in enumerate(names) if n.lower().startswith(lower)]
    return wrap(matches[0]) if len(matches) == 1 else None
