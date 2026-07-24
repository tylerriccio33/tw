"""Settlement markers: one per province, seated on the terrain, labelled.

`provinces.json` gives each province a 2D world position (no height — the sim is a
coordinate-free graph, so height is a render concern). We line-trace down onto the
terrain to seat the marker, then colour it by the owning faction and label it with
the city name. Labels are drawn with a `TextRenderActor` in plain ASCII — the old
canvas HUD taught us the medium font is ASCII-only, and keeping names ASCII means
no tofu boxes.
"""

from __future__ import annotations

import unreal

from . import _scene, config

_MARKER_MESH = "/Engine/BasicShapes/Cube.Cube"
_TRACE_TOP = 1_000_00.0  # 1 km up, in cm
_TRACE_BOTTOM = -50_000.0


def _palette() -> list[unreal.LinearColor]:
    doc = config.load_json("provinces.json")
    return [
        unreal.LinearColor(c["r"], c["g"], c["b"], 1.0)
        for c in doc["faction_colors"]  # type: ignore[index]
    ]


def _seat(x: float, y: float) -> float:
    """Terrain height at (x,y) via a downward line trace; 0 if nothing is hit."""
    start = unreal.Vector(x, y, _TRACE_TOP)
    end = unreal.Vector(x, y, _TRACE_BOTTOM)
    hit = unreal.SystemLibrary.line_trace_single(
        unreal.EditorLevelLibrary.get_editor_world(),
        start,
        end,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        False,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    return hit.to_dict()["impact_point"].z if hit else 0.0


def _owner_of(snapshot: dict, province_id: int) -> int:
    for p in snapshot["provinces"]:
        if p["id"] == province_id:
            return p["owner"]
    return 0


def build(snapshot: dict) -> int:
    _scene.clear("markers")
    doc = config.load_json("provinces.json")
    palette = _palette()
    marker_mesh = unreal.load_asset(_MARKER_MESH)

    for prov in doc["provinces"]:  # type: ignore[index]
        x, y = prov["pos"][0], prov["pos"][1]
        z = _seat(x, y) + 120.0
        owner = _owner_of(snapshot, prov["id"])
        color = palette[owner] if owner < len(palette) else unreal.LinearColor(1, 1, 1, 1)

        pin = _scene.spawn(
            unreal.StaticMeshActor,
            unreal.Vector(x, y, z),
            layer="markers",
            label=f"TW_Marker_{prov['name']}",
        )
        comp = pin.static_mesh_component
        comp.set_static_mesh(marker_mesh)
        pin.set_actor_scale3d(unreal.Vector(1.4, 1.4, 2.4))
        mid = comp.create_and_set_material_instance_dynamic_from_material(
            0, unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
        )
        mid.set_vector_parameter_value("Color", color)

        label = _scene.spawn(
            unreal.TextRenderActor,
            unreal.Vector(x, y, z + 260.0),
            unreal.Rotator(0.0, 90.0, 0.0),
            layer="markers",
            label=f"TW_Label_{prov['name']}",
        )
        tr = label.text_render
        tr.set_text(unreal.Text(str(prov["name"])))
        tr.set_horizontal_alignment(unreal.HorizTextAligment.EHTA_CENTER)
        tr.set_text_render_color(unreal.Color(255, 255, 255, 255))
        tr.set_world_size(180.0)

    n = len(doc["provinces"])  # type: ignore[index,arg-type]
    unreal.log(f"[tw] markers: {n} settlements")
    return n
