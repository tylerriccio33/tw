"""Province borders: the baked border runs -> faction-coloured ribbons.

`province_borders.json` is ~21 runs of ``{a, b, points}`` (points already carry
terrain height). Each run is coloured by the owner of province ``a`` in the
current snapshot, using the faction palette in ``provinces.json``. Segments are
batched into one Instanced-Static-Mesh per colour (a `MaterialInstanceDynamic`
off ``M_Border``), so 21 runs cost a handful of draws, not hundreds of actors.

Borders are the ownership-dependent layer: static geography is built once, but
this is rebuilt whenever ownership changes (see `campaign.apply_snapshot`).
"""

from __future__ import annotations

import math

import unreal

from . import _scene, config

_SEG_MESH = "/Engine/BasicShapes/Cube.Cube"
_WIDTH_CM = 45.0
_HEIGHT_CM = 20.0
_Z_LIFT = 60.0


def _palette() -> list[unreal.LinearColor]:
    doc = config.load_json("provinces.json")
    return [
        unreal.LinearColor(c["r"], c["g"], c["b"], 1.0)
        for c in doc["faction_colors"]  # type: ignore[index]
    ]


def _owner_of(snapshot: dict, province_id: int) -> int:
    for p in snapshot["provinces"]:
        if p["id"] == province_id:
            return p["owner"]
    return 0


def build(snapshot: dict) -> int:
    _scene.clear("borders")
    borders = config.load_json("province_borders.json")
    palette = _palette()
    base_mat = unreal.load_asset("/Game/Generated/Materials/M_Border")
    mesh = unreal.load_asset(_SEG_MESH)

    per_color: dict[int, unreal.HierarchicalInstancedStaticMeshComponent] = {}

    def ism_for(owner: int) -> unreal.HierarchicalInstancedStaticMeshComponent:
        if owner in per_color:
            return per_color[owner]
        actor = _scene.spawn(unreal.Actor, layer="borders", label=f"TW_Border_{owner}")
        comp = unreal.HierarchicalInstancedStaticMeshComponent()
        actor.set_editor_property("root_component", comp)
        comp.set_static_mesh(mesh)
        if base_mat:
            mid = unreal.MaterialInstanceDynamic.create(base_mat, actor)
            color = palette[owner] if owner < len(palette) else unreal.LinearColor(1, 1, 1, 1)
            mid.set_vector_parameter_value("Color", color)
            comp.set_material(0, mid)
        per_color[owner] = comp
        return comp

    segments = 0
    for run in borders:  # type: ignore[union-attr]
        owner = _owner_of(snapshot, run["a"])
        comp = ism_for(owner)
        pts = run["points"]
        for p0, p1 in zip(pts, pts[1:]):
            a = _scene.vec(p0) + unreal.Vector(0, 0, _Z_LIFT)
            b = _scene.vec(p1) + unreal.Vector(0, 0, _Z_LIFT)
            mid = (a + b) * 0.5
            d = b - a
            length = math.hypot(d.x, d.y) or 1.0
            yaw = math.degrees(math.atan2(d.y, d.x))
            # Engine cube is 100 cm; scale to segment length x width x height.
            scale = unreal.Vector(length / 100.0, _WIDTH_CM / 100.0, _HEIGHT_CM / 100.0)
            comp.add_instance(unreal.Transform(mid, unreal.Rotator(0, 0, yaw), scale))
            segments += 1
    unreal.log(f"[tw] borders: {segments} segments in {len(per_color)} colours")
    return segments
