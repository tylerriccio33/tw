"""Terrain: the baked ``terrain.obj`` -> a Nanite static mesh.

`bake` writes ``terrain.obj`` (already in Unreal cm at the world origin) plus
``terrain_meta.json``. This turns the OBJ into ground.

There is exactly one path, on purpose. An `ALandscape` cannot be spawned outside
an interactive editor — placement goes through UE's actor-placement factory,
which touches `GLevelEditorModeTools()` and is a fatal engine assert (SIGSEGV,
not a catchable Python exception) in a commandlet. Since `twctl build`/`shot` are
commandlets, the landscape path could never run in the loop that actually
produces the golden shots, so it is not here. The terrain material reads world-Z
rather than painted weightmaps, so nothing downstream misses the landscape.

Every step verifies its own result and raises. A silent no-op here is expensive:
it yields a level with no ground and a `build_world` that logs a clean "done",
which is exactly how five all-black golden shots got blessed.
"""

from __future__ import annotations

import unreal

from . import _scene, config
from .materials import terrain as mat_terrain

TERRAIN_LABEL = "TW_Terrain"
TERRAIN_ASSET = f"{config.GENERATED_PACKAGE}/Terrain/SM_Terrain"


def build() -> unreal.Actor:
    """Import terrain.obj as a Nanite static mesh and place it at the origin."""
    _scene.clear("terrain")

    source = config.map_file("terrain.obj")
    if not source.is_file():
        raise FileNotFoundError(f"{source} missing — run `make bake` first")

    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path = f"{config.GENERATED_PACKAGE}/Terrain"
    task.destination_name = "SM_Terrain"
    task.automated = True
    task.replace_existing = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])

    # The OBJ import fails *without raising*: UE 5.8's Interchange OBJ translator
    # hits a handled ensure (e.g. `UVs.IsValidIndex(VertexData.UVIndex)` when the
    # OBJ carries no `vt` channel), logs it, and leaves no asset behind. Check.
    mesh = unreal.load_asset(TERRAIN_ASSET)
    if not isinstance(mesh, unreal.StaticMesh):
        raise RuntimeError(
            f"terrain import produced no StaticMesh at {TERRAIN_ASSET} "
            f"(got {mesh!r}); check the log for an Interchange OBJ ensure"
        )
    mesh.set_editor_property("nanite_settings", unreal.MeshNaniteSettings(enabled=True))

    actor = _scene.spawn(unreal.StaticMeshActor, layer="terrain", label=TERRAIN_LABEL)
    actor.static_mesh_component.set_static_mesh(mesh)

    # The OBJ carries no material, so the imported mesh keeps UE's default
    # WorldGridMaterial — a flat grey grid, which is what the map rendered as
    # once the sun was aimed at the ground again. `world.build_world` builds the
    # materials before the terrain, so M_Terrain is expected to exist by now.
    material = unreal.load_asset(mat_terrain.MATERIAL_PATH)
    if not isinstance(material, unreal.MaterialInterface):
        raise RuntimeError(
            f"terrain material missing at {mat_terrain.MATERIAL_PATH} "
            f"(got {material!r}); materials must be built before terrain"
        )
    actor.static_mesh_component.set_material(0, material)
    unreal.log(f"[tw] terrain: static mesh {TERRAIN_ASSET}")
    return actor
