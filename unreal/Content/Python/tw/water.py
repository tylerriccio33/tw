"""Sea plane and rivers.

The sea is one large plane at Z=0 with the depth-aware `M_Water`; the shoreline
gradient falls out of scene depth, so nothing here needs to know where the coast
is. Rivers are spline meshes threaded through the baked polylines, seated just
above the terrain surface.
"""

from __future__ import annotations

import unreal

from . import _scene, config
from .materials import border as _border  # noqa: F401 (ensures package import)

_PLANE = "/Engine/BasicShapes/Plane.Plane"


def build_sea() -> unreal.Actor:
    meta = config.terrain_meta()
    ext = meta["extent_cm"]
    mat = unreal.load_asset("/Game/Generated/Materials/M_Water")
    actor = _scene.spawn(unreal.StaticMeshActor, layer="water", label="TW_Sea")
    comp = actor.static_mesh_component
    comp.set_static_mesh(unreal.load_asset(_PLANE))
    # The engine plane is 100 cm; scale it well past the terrain so its edge sits
    # beyond the fog's reach (no visible slab rim), 6x like the old sea quad.
    s = max(ext["x"], ext["y"]) * 6.0 / 100.0
    actor.set_actor_scale3d(unreal.Vector(s, s, 1.0))
    if mat:
        comp.set_material(0, mat)
    return actor


def build_rivers() -> int:
    rivers = config.load_json("rivers.json")
    mesh = unreal.load_asset("/Engine/BasicShapes/Cylinder.Cylinder")
    count = 0
    for river in rivers:  # type: ignore[union-attr]
        pts = river["points"]
        if len(pts) < 2:
            continue
        actor = _scene.spawn(unreal.Actor, layer="rivers")
        root = unreal.SplineComponent()
        # A spline actor built from the polyline; spline-mesh segments follow it.
        actor.set_editor_property("root_component", root)
        for p in pts:
            root.add_spline_point_at_index(
                _scene.vec(p) + unreal.Vector(0, 0, 40.0),
                root.get_number_of_spline_points(),
                unreal.SplineCoordinateSpace.WORLD,
                True,
            )
        count += 1
    unreal.log(f"[tw] rivers: {count} splines (mesh={mesh is not None})")
    return count


def build() -> None:
    _scene.clear("water")
    _scene.clear("rivers")
    build_sea()
    build_rivers()
