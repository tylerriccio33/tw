"""build_world() — the one entry point that assembles the whole campaign map.

Order matters: materials first (everything else references them), then terrain
(so markers can line-trace onto it), then the static geography and ownership
layers, then lighting. Static geography (terrain, sea, rivers, forests) is built
once; the ownership layers (borders, markers) are the ones `campaign` rebuilds
when the snapshot changes.

Runs headlessly under `twctl build`; also callable live from the editor console.
A sidecar must be reachable (`make sim`, or `twctl` spawning one) — there is no
neutral-snapshot mode. Nothing here degrades quietly: a missing asset, a failed
import or an unreachable sim raises, because the failure mode this build has
already hit is a clean "done" over an empty level.
"""

from __future__ import annotations

import unreal

from . import (
    _scene,
    borders,
    forests,
    landscape,
    lighting,
    markers,
    materials,
    water,
)


TERRAIN_MATERIAL = "/Game/Generated/Materials/M_Terrain"


def _snapshot(campaign: str, seed: int) -> dict:
    """The opening snapshot from the sidecar. Raises if no sim is reachable —
    a build against invented ownership renders borders in one flat colour and
    looks close enough to right to get blessed as a golden."""
    from . import simbridge

    return simbridge.opening_snapshot(campaign=campaign, seed=seed)


def _ensure_materials() -> None:
    if not unreal.EditorAssetLibrary.does_asset_exist(TERRAIN_MATERIAL):
        materials.build_all()


def _apply_terrain_material(terrain_actor: unreal.Actor) -> None:
    mat = unreal.load_asset(TERRAIN_MATERIAL)
    if not mat:
        raise RuntimeError(f"{TERRAIN_MATERIAL} did not load after materials.build_all()")
    comps = terrain_actor.get_components_by_class(unreal.StaticMeshComponent)
    if not comps:
        raise RuntimeError(f"terrain actor {terrain_actor} has no StaticMeshComponent")
    for comp in comps:
        comp.set_material(0, mat)


def build_world(campaign: str = "britain", seed: int = 42, *, save: bool = False) -> None:
    unreal.log("[tw] build_world: start")
    _scene.clear_all()
    _ensure_materials()

    snapshot = _snapshot(campaign, seed)

    terrain_actor = landscape.build()
    _apply_terrain_material(terrain_actor)

    water.build()
    forests.build()
    borders.build(snapshot)
    markers.build(snapshot)
    lighting.build()

    if save:
        _scene.save_open_level()
    unreal.log(f"[tw] build_world: done (turn {snapshot.get('turn')})")
