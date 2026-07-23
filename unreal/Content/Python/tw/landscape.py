"""Terrain: the baked heightmap -> an Unreal Landscape (with a static-mesh
fallback).

`bake` writes ``heightmap.r16`` (raw 16-bit, Unreal-oriented) and
``terrain_meta.json`` (dims + Unreal-cm extent + the cm height range). This turns
that into ground.

Two paths:

* `build_landscape()` — the intended one: an `ALandscape` whose material can be
  RVT-backed and layer-blended (grass/arid/rock/snow). Scripted landscape import
  is the single most engine-version-sensitive call in the toolkit; it is isolated
  in `_import_heightmap` so it is the one thing to confirm in-editor on 5.8.
* `build_from_obj()` — the fallback: import the baked ``terrain.obj`` as a Nanite
  static mesh. Fully supported by the Python asset-import API, so the rest of the
  pipeline is never blocked on the landscape call.

`build()` tries the landscape and falls back, logging which path ran. Either way
the terrain lands at the same world transform, so borders/rivers/markers line up.
"""

from __future__ import annotations

import array

import unreal

from . import _scene, config

TERRAIN_LABEL = "TW_Terrain"


def _read_heightmap() -> tuple[array.array, dict]:
    meta = config.terrain_meta()
    hm = meta["heightmap"]
    data = array.array("H")  # unsigned 16-bit
    data.frombytes(config.map_file(hm["file"]).read_bytes())
    if len(data) != hm["width"] * hm["height"]:
        raise ValueError(
            f"heightmap is {len(data)} samples, expected "
            f"{hm['width']}x{hm['height']}={hm['width'] * hm['height']}"
        )
    # struct/array reads native byte order; the file is little-endian.
    import sys

    if sys.byteorder == "big":
        data.byteswap()
    return data, meta


def _transform(meta: dict) -> tuple[unreal.Vector, unreal.Vector]:
    """Landscape actor location + scale so the WxH heightmap spans the baked
    Unreal-cm extent in X/Y and the 16-bit range maps back to the cm height span.

    Unreal's landscape stores height as ``worldZ = (u16 - 32768) / 128 * scaleZ``
    at the actor transform, so to make the full 0..65535 range cover ``span`` cm
    we need ``scaleZ = span / 65535 * 128 / 100`` (the extra /100 because actor
    scale multiplies the built-in 1 uu quad, and 128 is the LANDSCAPE_ZSCALE)."""
    hm, ext, hcm = meta["heightmap"], meta["extent_cm"], meta["height_cm"]
    # Quads = samples - 1; scale.x/y = cm-per-quad / 100 (the 1uu authored quad).
    scale_x = ext["x"] / (hm["width"] - 1) / 100.0
    scale_y = ext["y"] / (hm["height"] - 1) / 100.0
    scale_z = hcm["span"] / 65535.0 * 128.0 / 100.0
    # Centre the footprint on the origin, and offset Z so u16==0 -> min cm.
    loc = unreal.Vector(-ext["x"] / 2.0, -ext["y"] / 2.0, hcm["min"] + hcm["span"] / 2.0)
    return loc, unreal.Vector(scale_x, scale_y, scale_z)


def _import_heightmap(
    data: array.array, meta: dict, loc: unreal.Vector, scale: unreal.Vector
) -> unreal.Actor:
    """Create the ALandscape from the height samples. ISOLATED ON PURPOSE — this
    is the call to verify against the installed engine's Python API; if 5.8 does
    not expose landscape height import, `build()` falls through to the static mesh.
    """
    hm = meta["heightmap"]
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    landscape = subsystem.spawn_actor_from_class(
        unreal.Landscape, loc, unreal.Rotator(0, 0, 0)
    )
    landscape.set_actor_scale3d(scale)
    landscape.set_actor_label(TERRAIN_LABEL)
    landscape.tags = [_scene.TW_TAG, unreal.Name("tw.terrain")]
    # The heightmap import itself. `import_heightmap` takes the raw u16 buffer and
    # the (width,height) it covers; a layer-less import is enough for a code-owned
    # material that reads world height rather than painted weightmaps.
    landscape.import_heightmap(list(data), hm["width"], hm["height"])  # verify on 5.8
    return landscape


def build_landscape() -> unreal.Actor:
    data, meta = _read_heightmap()
    loc, scale = _transform(meta)
    actor = _import_heightmap(data, meta, loc, scale)
    unreal.log(f"[tw] terrain: Landscape {meta['heightmap']['width']}x"
               f"{meta['heightmap']['height']} at scale {scale}")
    return actor


def build_from_obj() -> unreal.Actor:
    """Fallback: import terrain.obj as a Nanite static mesh and place it. The OBJ
    is already in Unreal cm at the world origin, so it needs no transform."""
    task = unreal.AssetImportTask()
    task.filename = str(config.map_file("terrain.obj"))
    task.destination_path = f"{config.GENERATED_PACKAGE}/Terrain"
    task.destination_name = "SM_Terrain"
    task.automated = True
    task.replace_existing = True
    task.save = True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    mesh = unreal.load_asset(f"{config.GENERATED_PACKAGE}/Terrain/SM_Terrain")
    if isinstance(mesh, unreal.StaticMesh):
        mesh.set_editor_property("nanite_settings", unreal.MeshNaniteSettings(enabled=True))
    actor = _scene.spawn(
        unreal.StaticMeshActor, layer="terrain", label=TERRAIN_LABEL
    )
    actor.static_mesh_component.set_static_mesh(mesh)
    unreal.log("[tw] terrain: static mesh (terrain.obj) fallback")
    return actor


def build() -> unreal.Actor:
    """Build the terrain, preferring the Landscape and falling back to the mesh."""
    _scene.clear("terrain")
    try:
        return build_landscape()
    except Exception as e:  # noqa: BLE001 - fall back on any landscape-API gap
        unreal.log_warning(f"[tw] landscape import unavailable ({e}); using OBJ mesh")
        return build_from_obj()
