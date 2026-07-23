"""Forests: the baked tree instances -> one Hierarchical Instanced Static Mesh.

`forests.json` is ~950 {pos, scale, yaw_deg} entries (already Unreal cm, seated on
the terrain). One HISM component draws them all in a single batch, which is how
the map affords that many trees. The mesh is a placeholder engine cone tinted
green until a real tree asset is imported — swap `_TREE_MESH` when one exists.
"""

from __future__ import annotations

import math

import unreal

from . import _scene, config

# Placeholder until a real foliage asset is imported (assets-as-code, later).
_TREE_MESH = "/Engine/BasicShapes/Cone.Cone"


def build() -> unreal.Actor:
    _scene.clear("forests")
    trees = config.load_json("forests.json")
    actor = _scene.spawn(unreal.Actor, layer="forests", label="TW_Forests")
    hism = unreal.HierarchicalInstancedStaticMeshComponent()
    actor.set_editor_property("root_component", hism)
    hism.set_static_mesh(unreal.load_asset(_TREE_MESH))
    for t in trees:  # type: ignore[union-attr]
        loc = _scene.vec(t["pos"])
        rot = unreal.Rotator(0.0, 0.0, float(t["yaw_deg"]))
        # A cone authored at ~100 cm; the baked scale is in tree-mesh units, made
        # a touch taller so canopies read from the campaign camera.
        s = float(t["scale"])
        xform = unreal.Transform(loc, rot, unreal.Vector(s, s, s * 1.6))
        hism.add_instance(xform)
    unreal.log(f"[tw] forests: {len(trees)} instances")  # type: ignore[arg-type]
    return actor
