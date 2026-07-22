"""The medieval Britain campaign, assembled from `campaign/britain.yaml`.

This module is the bridge from the plain dataclasses in `config` onto the
simulation's own types: it resolves owner/capital names to ids, maps tier
strings onto `SettlementTier`, and wires the adjacency graph. Everything it
reads is authored content, so a bad reference (an owner naming a faction that
does not exist) raises loudly rather than being smuggled through as a default.
"""

from __future__ import annotations

import random

from .config import balance, campaign
from .ids import ArmyId, FactionId, ProvinceId
from .state import (
    Army,
    City,
    DiploStatus,
    Faction,
    Force,
    GameState,
    Province,
    Relation,
    SettlementTier,
)
from . import turn

# Convenience handles for callers and tests. These are indices into the faction
# list in `campaign/britain.yaml` and must track its order (rebels last).
ENGLAND = FactionId(0)
SCOTLAND = FactionId(1)
WALES = FactionId(2)
IRELAND = FactionId(3)
REBELS = FactionId(4)

_TIERS = {
    "Village": SettlementTier.VILLAGE,
    "Town": SettlementTier.TOWN,
    "City": SettlementTier.CITY,
    "Capital": SettlementTier.CAPITAL,
}


def tier_from_str(s: str) -> SettlementTier:
    """Map a settlement-tier name from the campaign file onto the enum."""
    try:
        return _TIERS[s]
    except KeyError:
        raise ValueError(f"campaign uses unknown settlement tier {s!r}") from None


def general_for(owner: FactionId, id: ArmyId, name: str = "britain") -> str:
    """The general who takes command of army `id` for `owner`.

    A pure function of the two ids on purpose: drawing the name from the state's
    RNG would advance the seeded stream out from under everything else.
    """
    factions = campaign(name).factions
    pool = factions[owner % len(factions)].generals
    return pool[id % len(pool)]


def new_game(seed: int, name: str = "britain") -> GameState:
    camp = campaign(name)
    b = balance()

    def is_rebel(f: int) -> bool:
        return camp.factions[f].rebel

    factions = [
        Faction(
            name=f.name,
            treasury=0 if f.rebel else b.starting_treasury,
            is_player=f.player,
            is_rebel=f.rebel,
            alive=True,
        )
        for f in camp.factions
    ]

    provinces: list[Province] = []
    for spec in camp.provinces:
        owner = FactionId(camp.faction_index(spec.owner))
        developed = not is_rebel(owner)
        tier = tier_from_str(spec.tier)
        provinces.append(
            Province(
                name=spec.name,
                owner=owner,
                adjacent=[],
                base_income=spec.base_income,
                city=City(
                    name=spec.city,
                    tier=tier,
                    population=tier.base_population,
                    garrison=Force.of_melee(spec.garrison),
                    walls=spec.walls,
                    farm=1 if developed else 0,
                    market=1 if developed else 0,
                    barracks=1,
                    recruit_queue=Force(),
                    construction=None,
                ),
                siege=None,
            )
        )
    for a_name, b_name in camp.edges:
        a, bb = camp.province_index(a_name), camp.province_index(b_name)
        provinces[a].adjacent.append(ProvinceId(bb))
        provinces[bb].adjacent.append(ProvinceId(a))

    # Everyone is permanently at war with the rebels.
    relations: dict[frozenset[FactionId], Relation] = {}
    rebels = FactionId(camp.faction_index("Rebels"))
    for i in range(len(factions)):
        if not is_rebel(i):
            relations[frozenset((FactionId(i), rebels))] = Relation(DiploStatus.WAR, 0)

    # Each faction with a capital musters a founding army there. Army ids track
    # faction order so a given seed always leads the same generals.
    armies: dict[ArmyId, Army] = {}
    for i, f in enumerate(camp.factions):
        if f.capital is None:
            continue
        owner, id = FactionId(i), ArmyId(i)
        armies[id] = Army(
            owner=owner,
            location=ProvinceId(camp.province_index(f.capital)),
            force=Force.of_melee(b.starting_army),
            mp=b.army_mp,
            general=general_for(owner, id, name),
        )

    current = FactionId(
        next((i for i, f in enumerate(camp.factions) if f.player), 0)
    )

    state = GameState(
        turn=1,
        current=current,
        factions=factions,
        provinces=provinces,
        armies=armies,
        relations=relations,
        proposals=[],
        next_army=len(armies),
        next_proposal=0,
        rng=random.Random(seed),
        winner=None,
    )

    # Neighbors eye each other warily from day one.
    living = state.living_factions()
    for a in living:
        for bf in living:
            if a < bf and state.share_border(a, bf):
                state.shift_opinion(a, bf, -10)

    # Kick off the first faction's turn (income, movement points).
    turn.begin_turn(state)
    return state
