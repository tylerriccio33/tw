"""The sea/river water material — depth-aware, translucent, lightly specular.

Shallow water over the coast reads teal and near-opaque; it deepens to navy as
the sea floor drops away. Depth comes from `SceneDepth - PixelDepth` so a single
flat plane at Z=0 gives the shoreline gradient in target-state.png for free.
"""

from __future__ import annotations

import unreal

from . import _graph as g

MATERIAL_PATH = f"{g.MAT_PACKAGE}/M_Water"

SHALLOW = (0.06, 0.25, 0.40)
DEEP = (0.02, 0.08, 0.26)


def build() -> unreal.Material:
    mat = g.create_material("M_Water")
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)

    # Depth of water in front of this pixel, normalised over ~2000 cm.
    depth = g.node(mat, unreal.MaterialExpressionSceneDepth, -1000, -200)
    pixel = g.node(mat, unreal.MaterialExpressionPixelDepth, -1000, 0)
    sub = g.node(mat, unreal.MaterialExpressionSubtract, -700, -100)
    g.link(depth, sub, "A")
    g.link(pixel, sub, "B")
    div = g.node(mat, unreal.MaterialExpressionDivide, -500, -100)
    g.link(sub, div, "A")
    div.set_editor_property("const_b", 2000.0)
    alpha = g.node(mat, unreal.MaterialExpressionSaturate, -300, -100)
    g.link(div, alpha, "")

    shallow = g.color(mat, SHALLOW, -700, 200)
    deep = g.color(mat, DEEP, -700, 400)
    base = g.lerp(mat, shallow, deep, alpha, -100, 200)
    g.to_property(base, unreal.MaterialProperty.MP_BASE_COLOR)

    # Near-opaque at the coast, translucent in the shallows.
    op = g.node(mat, unreal.MaterialExpressionLinearInterpolate, 100, 400)
    op.set_editor_property("const_a", 0.6)
    op.set_editor_property("const_b", 0.95)
    g.link(alpha, op, "Alpha")
    g.to_property(op, unreal.MaterialProperty.MP_OPACITY)

    g.to_property(g.const(mat, 0.04, 100, 600), unreal.MaterialProperty.MP_ROUGHNESS)
    # Not metallic: a metallic surface has no diffuse albedo, so the blue above
    # was being thrown away and the sea rendered as a dark mirror of the sky.
    g.to_property(g.const(mat, 0.0, 100, 700), unreal.MaterialProperty.MP_METALLIC)

    g.recompile(mat)
    unreal.log("[tw] M_Water built")
    return mat
