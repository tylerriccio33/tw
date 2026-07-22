"""Data-driven campaign configuration, loaded from the YAML under `sim/campaign/`.

This is the home for everything that would otherwise be a constant: balance
knobs (`balance.yaml`) and the campaign itself — factions, provinces, borders
(`britain.yaml`).

The Rust engine embedded these with `include_str!`; Python reads them at import
time instead and caches the parse behind an `lru_cache`. Authored content is
trusted: a malformed file or an unknown province/faction name is a content bug
we control, so it raises loudly rather than being threaded through every caller
as an error value.

These are plain dataclasses of strings and numbers. Mapping the strings onto
engine types (`FactionId`, `SettlementTier`) happens in `data.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

CAMPAIGN_DIR = Path(
    os.environ.get("TW_CAMPAIGN_DIR", Path(__file__).resolve().parents[2] / "campaign")
)


@dataclass(frozen=True, slots=True)
class Balance:
    """Every balance tuning knob. Fields mirror `balance.yaml` one-to-one."""

    melee_cost: int
    archer_cost: int
    cav_cost: int
    #: Population that yields one unit of per-turn recruitment throughput.
    recruit_pop_per_unit: int
    regiment_upkeep: int
    garrison_upkeep: int
    farm_income: int
    market_income: int
    trade_bonus: int
    tax_per_thousand: int
    build_cost_per_level: int
    max_building_level: int
    max_walls: int
    wall_defense_pct: int
    army_mp: int
    siege_attrition_after: int
    starting_treasury: int
    starting_army: int
    win_provinces: int


@dataclass(frozen=True, slots=True)
class FactionDef:
    """A playable or rebel faction, in authoritative order (see `britain.yaml`)."""

    name: str
    #: Names its generals cycle through, in army-id order.
    generals: list[str]
    #: The human player's faction. At most one.
    player: bool = False
    #: The frontier rebels: no treasury, permanently at war with everyone.
    rebel: bool = False
    #: Province name where this faction's founding army musters. Rebels have none.
    capital: str | None = None


@dataclass(frozen=True, slots=True)
class ProvinceDef:
    """A province and the settlement that holds it."""

    name: str
    city: str
    #: `Village | Town | City | Capital`, mapped to `SettlementTier` in `data`.
    tier: str
    #: Owning faction, by name.
    owner: str
    base_income: int
    garrison: int
    walls: int


@dataclass(frozen=True, slots=True)
class Campaign:
    """The whole campaign: who is playing, the land they hold, and how it connects."""

    factions: list[FactionDef]
    provinces: list[ProvinceDef]
    #: Undirected borders, each a pair of province names.
    edges: list[tuple[str, str]] = field(default_factory=list)

    def province_index(self, name: str) -> int:
        """Index of a province by name, raising on a typo — the campaign is
        authored data we control, so an unknown name is a content bug to surface
        loudly, not a runtime condition to handle."""
        for i, p in enumerate(self.provinces):
            if p.name == name:
                return i
        raise KeyError(f"campaign references unknown province {name!r}")

    def faction_index(self, name: str) -> int:
        """Index of a faction by name, raising on a typo (see `province_index`)."""
        for i, f in enumerate(self.factions):
            if f.name == name:
                return i
        raise KeyError(f"campaign references unknown faction {name!r}")


def _load(name: str) -> object:
    path = CAMPAIGN_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=None)
def balance() -> Balance:
    """The balance knobs, parsed once."""
    return Balance(**_load("balance.yaml"))


@lru_cache(maxsize=None)
def campaign(name: str = "britain") -> Campaign:
    """The campaign definition, parsed once per name."""
    raw = _load(f"{name}.yaml")
    return Campaign(
        factions=[FactionDef(**f) for f in raw["factions"]],
        provinces=[ProvinceDef(**p) for p in raw["provinces"]],
        edges=[(a, b) for a, b in raw["edges"]],
    )
