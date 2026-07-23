"""Paths and constants shared across the tw toolkit.

Single source of truth for *where things are* (so nothing hard-codes a path) and
for the handful of numbers that cross the language boundary from the baker. The
package lives at ``<repo>/unreal/Content/Python/tw``; everything is resolved
relative to that so the toolkit does not care where the editor was launched from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# <repo>/unreal/Content/Python/tw/config.py -> parents[4] == <repo>
REPO = Path(__file__).resolve().parents[4]
UNREAL = REPO / "unreal"
MAP_DIR = UNREAL / "Content" / "Map"
SHOTS_DIR = UNREAL / "Shots"
SHOTS_CURRENT = SHOTS_DIR / "current"
SHOTS_GOLDEN = SHOTS_DIR / "golden"

SIM_DIR = REPO / "sim"
SIM_PORT_FILE = SIM_DIR / ".sim-port"

# Where content the toolkit generates (materials, RVT, master meshes) is saved.
GENERATED_PACKAGE = "/Game/Generated"


def map_file(name: str) -> Path:
    """A bake output under Content/Map, e.g. ``map_file("provinces.json")``."""
    return MAP_DIR / name


def load_json(name: str) -> object:
    """Load a JSON bake output by filename."""
    with open(map_file(name), "r", encoding="utf-8") as f:
        return json.load(f)


def terrain_meta() -> dict:
    """`terrain_meta.json` — heightmap dims, Unreal-cm extent, height range and
    the material's height bands. Written by the baker; see bake/src/main.rs."""
    return load_json("terrain_meta.json")  # type: ignore[return-value]


def sim_port(default: int | None = None) -> int | None:
    """The port a `tw_sim.server` sidecar advertised, or ``default``.

    Order: the ``TW_SIM_PORT`` env var (how `twctl` forwards a spawned sidecar),
    then ``sim/.sim-port`` (how a hand-started `make sim` advertises itself)."""
    env = os.environ.get("TW_SIM_PORT")
    if env:
        return int(env)
    try:
        return int(SIM_PORT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return default
