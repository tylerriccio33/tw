"""The province-border material — a flat, faintly emissive ribbon whose colour is
an instance parameter, so one material tints to every faction.

`borders.py` makes a `MaterialInstanceDynamic` per border run and sets ``Color``
from the owning faction's palette entry (provinces.json ``faction_colors``).
"""

from __future__ import annotations

import unreal

from . import _graph as g

MATERIAL_PATH = f"{g.MAT_PACKAGE}/M_Border"
COLOR_PARAM = "Color"


def build() -> unreal.Material:
    mat = g.create_material("M_Border")
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    # Unlit: a map-overlay line should not take world lighting.
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

    color = g.node(mat, unreal.MaterialExpressionVectorParameter, -500, 0)
    color.set_editor_property("parameter_name", unreal.Name(COLOR_PARAM))
    color.set_editor_property("default_value", unreal.LinearColor(0.9, 0.8, 0.1, 1.0))
    g.to_property(color, unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    g.to_property(g.const(mat, 0.85, -200, 300), unreal.MaterialProperty.MP_OPACITY)

    g.recompile(mat)
    unreal.log("[tw] M_Border built")
    return mat
