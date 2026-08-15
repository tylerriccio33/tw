#!/usr/bin/env python3
"""Generates the deterministic sandbox map package under campaign/map_data_sandbox/.

A hand-built package (no map editor, no backdrop art) for playtesting and
manual QA. A 4x4 grid of square provinces splits into four 2x2 quadrants,
one per faction. Each quadrant's provinces all start owned by that faction.
`start_game_from_provinces` (rust/campaign/src/godot_api.rs) then mints one
city per province automatically. That's 4 cities per faction, tiers cycling
1/2/3/5 in the same grid position in every quadrant.

Grid math (ids, neighbors, tiers, owners) never varies, so re-running this
script is a no-op: same bytes every time. The exception is the "resources"
landmarks - a seeded RNG places those for visual variety, see RESOURCE_SEED.
Run directly (`uv run tools/generate_sandbox_map.py` or plain `python3`);
`make play-sandbox` runs it before every launch.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "campaign" / "map_data_sandbox"

GRID = 4  # provinces per side -> 4x4 = 16 total, one 2x2 quadrant per faction
TILE = 500  # px per province tile
MAP_SIZE = GRID * TILE

RESOURCE_SEED = 20260814
NUM_RESOURCES = 10

FACTIONS = [
    {"key": "crimson", "name": "Crimson Realm", "color": "#e6194b", "money": 100},
    {"key": "azure", "name": "Azure Dominion", "color": "#4363d8", "money": 100},
    {"key": "verdant", "name": "Verdant League", "color": "#3cb44b", "money": 100},
    {"key": "amber", "name": "Amber Concord", "color": "#f58231", "money": 100},
]

# Tier assigned to a province by its position within its own 2x2 quadrant
# (row, col both 0 or 1) - the same pattern in every quadrant, so all four
# factions start with an identical 1/2/3/5 tier spread.
TIER_BY_QUADRANT_CELL = {
    (0, 0): 1,
    (0, 1): 2,
    (1, 0): 3,
    (1, 1): 5,
}

RESOURCE_KINDS = [
    ("iron", "#9aa0a6"),
    ("timber", "#7b3f00"),
    ("wine", "#8e2f4a"),
    ("salt", "#e8e8e8"),
    ("wool", "#c9b79c"),
    ("fish", "#4a7fb5"),
]


def faction_for(row: int, col: int) -> dict:
    top = row < GRID // 2
    left = col < GRID // 2
    if top and left:
        return FACTIONS[0]
    if top and not left:
        return FACTIONS[1]
    if not top and left:
        return FACTIONS[2]
    return FACTIONS[3]


def build_provinces() -> list[dict]:
    provinces = []
    for row in range(GRID):
        for col in range(GRID):
            province_id = row * GRID + col + 1
            faction = faction_for(row, col)
            quad_cell = (row % 2, col % 2)
            tier = TIER_BY_QUADRANT_CELL[quad_cell]

            x0, y0 = col * TILE, row * TILE
            x1, y1 = x0 + TILE, y0 + TILE
            cx, cy = x0 + TILE / 2, y0 + TILE / 2

            neighbors = []
            if row > 0:
                neighbors.append(province_id - GRID)
            if row < GRID - 1:
                neighbors.append(province_id + GRID)
            if col > 0:
                neighbors.append(province_id - 1)
            if col < GRID - 1:
                neighbors.append(province_id + 1)

            city_index = quad_cell[0] * 2 + quad_cell[1] + 1
            provinces.append(
                {
                    "id": province_id,
                    "key": f"province_{province_id}",
                    "name": f"{faction['name']} Province {province_id}",
                    "centroid": [cx, cy],
                    "area_px": TILE * TILE,
                    "neighbors": neighbors,
                    "tags": {"terrain": "plains"},
                    "starting_owner": faction["key"],
                    "city_position": [cx, cy],
                    "tier": tier,
                    "city_name": f"{faction['name']} City {city_index}",
                    "_rings": [[x0, y0, x1, y0, x1, y1, x0, y1]],
                }
            )
    return provinces


def build_points(rng: random.Random) -> dict:
    resources = []
    for i in range(NUM_RESOURCES):
        kind, _ = RESOURCE_KINDS[i % len(RESOURCE_KINDS)]
        resources.append(
            {
                "id": f"r{i + 1}",
                "x": round(rng.uniform(0, MAP_SIZE), 2),
                "y": round(rng.uniform(0, MAP_SIZE), 2),
                "kind": kind,
                "name": f"Sandbox {kind.capitalize()} {i + 1}",
            }
        )
    return {"format_version": 1, "layers": {"resources": resources}}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n")


def main() -> None:
    provinces = build_provinces()

    manifest = {
        "format_version": 1,
        "size": [MAP_SIZE, MAP_SIZE],
        "factions": "factions.json",
        "province_layer": "provinces",
        "city_layer": "cities",
        "layers": ["resources"],
    }
    write_json(OUT_DIR / "map.json", manifest)
    write_json(OUT_DIR / "factions.json", FACTIONS)

    table = {
        "provinces": [{k: v for k, v in p.items() if k != "_rings"} for p in provinces]
    }
    write_json(OUT_DIR / "provinces.table.json", table)

    geo = {
        "format_version": 1,
        "size": [MAP_SIZE, MAP_SIZE],
        "provinces": [
            {"id": p["id"], "rings": [{"points": p["_rings"][0], "hole": False}]}
            for p in provinces
        ],
    }
    write_json(OUT_DIR / "provinces.geo.json", geo)

    rng = random.Random(RESOURCE_SEED)
    write_json(OUT_DIR / "points.json", build_points(rng))

    resources_legend = {
        color: {"key": kind, "name": kind.capitalize()}
        for kind, color in RESOURCE_KINDS
    }
    write_json(
        OUT_DIR / "layers" / "resources.json",
        {
            "name": "resources",
            "title": "Resources",
            "input": "point",
            "kind": "class",
            "point_coupling": "free",
            "legend": resources_legend,
        },
    )

    print(f"wrote sandbox map package to {OUT_DIR}")


if __name__ == "__main__":
    main()
