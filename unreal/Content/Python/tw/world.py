"""build_world() — the one entry point that assembles the whole campaign map.

Order matters: materials first (everything else references them), then terrain
(so markers can line-trace onto it), then the static geography and ownership
layers, then lighting. Static geography (terrain, sea, rivers, forests) is built
once; the ownership layers (borders, markers) are the ones `campaign` rebuilds
when the snapshot changes.

Runs headlessly under `twctl build`; also callable live from the editor console.
If no sidecar is reachable it still builds the geography against a neutral
snapshot, so the visual loop never depends on the sim being up.
"""

from __future__ import annotations

import unreal

from . import (
    _scene,
    borders,
    config,
    forests,
    landscape,
    lighting,
    markers,
    materials,
    water,
)


def _neutral_snapshot() -> dict:
    """Everything owned by faction 0 — enough to render geography when the sim is
    not running (visuals-first builds do not need live ownership)."""
    doc = config.load_json("provinces.json")
    return {
        "turn": 0,
        "provinces": [{"id": p["id"], "owner": 0} for p in doc["provinces"]],  # type: ignore[index]
    }


def _snapshot(campaign: str, seed: int) -> dict:
    from . import simbridge

    try:
        return simbridge.opening_snapshot(campaign=campaign, seed=seed)
    except Exception as e:  # noqa: BLE001
        unreal.log_warning(f"[tw] no sim ({e}); building against a neutral snapshot")
        return _neutral_snapshot()


def _ensure_materials() -> None:
    if not unreal.EditorAssetLibrary.does_asset_exist("/Game/Generated/Materials/M_Terrain"):
        materials.build_all()


def _apply_terrain_material(terrain_actor: unreal.Actor) -> None:
    mat = unreal.load_asset("/Game/Generated/Materials/M_Terrain")
    if not mat:
        return
    # Landscape and the static-mesh fallback take the material differently.
    if isinstance(terrain_actor, unreal.Landscape):
        terrain_actor.set_editor_property("landscape_material", mat)
    else:
        for comp in terrain_actor.get_components_by_class(unreal.StaticMeshComponent):
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
