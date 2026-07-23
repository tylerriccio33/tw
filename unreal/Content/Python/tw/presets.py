"""Named fixed-camera shots — the comparable views the visual loop is built on.

"Editing how the game looks" is edit -> view -> iterate, and *view* has to be one
command with a fixed camera or two runs are not comparable. Each preset is a
(location, rotation, fov) tuple in Unreal cm/deg, tuned to frame a distinct part
of the map (the whole thing, the lowlands, a coast, the highlands, a border).

Positions are derived from the baked Unreal-cm extent so they stay sensible if the
map extent changes; tweak the fractions, not raw coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Shot:
    name: str
    location: tuple[float, float, float]
    rotation: tuple[float, float, float]  # pitch, yaw, roll
    fov: float = 40.0


def all_shots() -> list[Shot]:
    ext = config.terrain_meta()["extent_cm"]
    hx, hy = ext["x"] / 2.0, ext["y"] / 2.0  # half-extents (X short, Y long)
    high = max(hx, hy)

    return [
        # The whole campaign, high and looking down the long axis.
        Shot("overview", (-hx * 1.7, 0.0, high * 1.5), (-40.0, 0.0, 0.0), 45.0),
        # Low, raking across the southern lowlands.
        Shot("lowlands", (-hx * 0.2, -hy * 0.4, high * 0.35), (-22.0, 20.0, 0.0), 38.0),
        # A coastline, camera out over the sea looking back at land.
        Shot("coast", (hx * 1.2, hy * 0.2, high * 0.30), (-18.0, 200.0, 0.0), 40.0),
        # The highlands — the only place rock/snow bands are reachable.
        Shot("mountain", (-hx * 0.3, hy * 0.55, high * 0.45), (-30.0, 150.0, 0.0), 36.0),
        # Tight on a province border, to catch the coloured ribbons.
        Shot("border", (0.0, 0.0, high * 0.25), (-35.0, 90.0, 0.0), 34.0),
    ]


def by_name(names: list[str]) -> list[Shot]:
    table = {s.name: s for s in all_shots()}
    missing = [n for n in names if n not in table]
    if missing:
        raise KeyError(f"unknown preset(s): {missing}; have {sorted(table)}")
    return [table[n] for n in names]


def names() -> list[str]:
    return [s.name for s in all_shots()]
