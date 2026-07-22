"""Where a faction's gold comes from, and where it goes."""

from __future__ import annotations

from .config import balance
from .ids import FactionId
from .state import GameState


def province_income(state: GameState, p: int) -> int:
    b = balance()
    prov = state.provinces[p]
    return (
        prov.base_income
        + tax_income(state, p)
        + prov.city.farm * b.farm_income
        + prov.city.market * b.market_income
    )


def tax_income(state: GameState, p: int) -> int:
    """The tax the crown levies on a province's settlement, scaled by its
    population: bigger cities house more people and so yield more tax."""
    return state.provinces[p].city.population * balance().tax_per_thousand // 1000


def faction_income(state: GameState, f: FactionId) -> int:
    provinces = sum(
        province_income(state, i)
        for i in range(len(state.provinces))
        if state.provinces[i].owner == f
    )
    trade = (
        sum(
            1
            for other in state.living_factions()
            if other != f and state.trading(f, other) and state.share_border(f, other)
        )
        * balance().trade_bonus
    )
    return provinces + trade


def faction_upkeep(state: GameState, f: FactionId) -> int:
    b = balance()
    field = (
        sum(a.force.total() for a in state.armies.values() if a.owner == f)
        * b.regiment_upkeep
    )
    garrisons = (
        sum(p.city.garrison.total() for p in state.provinces if p.owner == f)
        * b.garrison_upkeep
    )
    return field + garrisons
