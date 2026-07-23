"""The terrain material — the single biggest lever toward target-state.png.

A world-height band blend (grass -> arid -> rock) with a slope-driven rock
override and a snow cap on the highest, flattest ground, exactly the palette in
the reference. The height thresholds are read from ``terrain_meta.json`` at build
time and expressed as fractions of the *actual* baked height range — so it bands
correctly whether the map is low (Britain peaks at ~2500 cm) or a full-relief
continent, instead of hard-coding the European anchors that the map may never
reach. That is the EXAG coupling made explicit on the Unreal side.
"""

from __future__ import annotations

import unreal

from . import _graph as g

MATERIAL_PATH = f"{g.MAT_PACKAGE}/M_Terrain"

# target-state palette, linear RGB.
GRASS = (0.09, 0.16, 0.05)
ARID = (0.30, 0.26, 0.12)
ROCK = (0.14, 0.12, 0.10)
SNOW = (0.72, 0.74, 0.78)


def build() -> unreal.Material:
    from .. import config

    meta = config.terrain_meta()
    lo = max(meta["height_cm"]["min"], 0.0)  # sea floor is negative; band on land
    hi = meta["height_cm"]["max"]
    span = max(hi - lo, 1.0)

    # Fractional band edges over the real land range (not absolute cm).
    grass_top = lo + 0.20 * span
    arid_top = lo + 0.55 * span
    rock_top = lo + 0.80 * span
    snow_lo = lo + 0.82 * span

    mat = g.create_material("M_Terrain")

    z = g.world_z(mat, -1400, 0)
    grass = g.color(mat, GRASS, -600, -400)
    arid = g.color(mat, ARID, -600, -200)
    rock = g.color(mat, ROCK, -600, 0)
    snow = g.color(mat, SNOW, -600, 200)

    # grass -> arid -> rock as height climbs.
    a1 = g.linstep(mat, grass_top, arid_top, z, -1000, -300)
    base = g.lerp(mat, grass, arid, a1, -300, -300)
    a2 = g.linstep(mat, arid_top, rock_top, z, -1000, -100)
    base2 = g.lerp(mat, base, rock, a2, -100, -200)

    # Slope override: steep ground reads as bare rock regardless of height.
    #   1 - VertexNormalWS.Z, thresholded high because heights are exaggerated.
    n = g.node(mat, unreal.MaterialExpressionVertexNormalWS, -1400, 500)
    nz = g.node(mat, unreal.MaterialExpressionComponentMask, -1200, 500)
    nz.set_editor_property("r", False)
    nz.set_editor_property("g", False)
    nz.set_editor_property("b", True)
    nz.set_editor_property("a", False)
    g.link(n, nz, "")
    one_minus = g.node(mat, unreal.MaterialExpressionOneMinus, -1000, 500)
    g.link(nz, one_minus, "")
    slope = g.linstep(mat, 0.55, 0.80, one_minus, -800, 500)
    rocky = g.lerp(mat, base2, rock, slope, 100, 0)

    # Snow cap on the highest, flattest ground.
    snow_h = g.linstep(mat, snow_lo, hi, z, -1000, 300)
    flat = g.node(mat, unreal.MaterialExpressionOneMinus, -600, 500)
    g.link(slope, flat, "")
    snow_mask = g.node(mat, unreal.MaterialExpressionMultiply, -400, 400)
    g.link(snow_h, snow_mask, "A")
    g.link(flat, snow_mask, "B")
    final = g.lerp(mat, rocky, snow, snow_mask, 300, 200)

    g.to_property(final, unreal.MaterialProperty.MP_BASE_COLOR)

    rough = g.const(mat, 0.92, 300, 500)
    g.to_property(rough, unreal.MaterialProperty.MP_ROUGHNESS)

    g.recompile(mat)
    unreal.log(
        f"[tw] M_Terrain: bands cm grass<{grass_top:.0f} arid<{arid_top:.0f} "
        f"rock<{rock_top:.0f} snow>{snow_lo:.0f} (range {lo:.0f}..{hi:.0f})"
    )
    return mat
