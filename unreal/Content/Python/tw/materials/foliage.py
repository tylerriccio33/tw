"""The tree-canopy material.

The baked forest instances were drawing in UE's default grey, which is a large
share of why the map read as monochrome: the reference's most saturated, most
recognisable feature is dense dark-green woodland stippled across the tan.

Flat and rough on purpose — the canopy is read at campaign-camera distance as a
mass of colour, not as individually shaded trees.
"""

from __future__ import annotations

import unreal

from . import _graph as g

MATERIAL_PATH = f"{g.MAT_PACKAGE}/M_Foliage"

CANOPY = (0.06, 0.13, 0.035)


def build() -> unreal.Material:
    mat = g.create_material("M_Foliage")

    g.to_property(g.color(mat, CANOPY, -400, 0), unreal.MaterialProperty.MP_BASE_COLOR)
    g.to_property(g.const(mat, 0.95, -400, 200), unreal.MaterialProperty.MP_ROUGHNESS)

    g.recompile(mat)
    unreal.log("[tw] M_Foliage built")
    return mat
