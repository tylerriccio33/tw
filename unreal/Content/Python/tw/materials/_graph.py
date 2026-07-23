"""Thin, readable wrappers over `unreal.MaterialEditingLibrary`.

The raw API is verbose (create expression at x,y; connect output pin to input
pin by string name). These helpers make a graph read top-to-bottom like the
shader it is.
"""

from __future__ import annotations

import unreal

from .. import config

MEL = unreal.MaterialEditingLibrary
_ASSET_TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
MAT_PACKAGE = f"{config.GENERATED_PACKAGE}/Materials"


def create_material(name: str) -> unreal.Material:
    """Create (replacing) an empty Material asset."""
    path = f"{MAT_PACKAGE}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    return _ASSET_TOOLS.create_asset(name, MAT_PACKAGE, unreal.Material, unreal.MaterialFactoryNew())


def node(mat: unreal.Material, cls: type, x: int, y: int) -> unreal.MaterialExpression:
    return MEL.create_material_expression(mat, cls, x, y)


def const(mat, value: float, x: int, y: int) -> unreal.MaterialExpression:
    n = node(mat, unreal.MaterialExpressionConstant, x, y)
    n.set_editor_property("r", float(value))
    return n


def color(mat, rgb: tuple[float, float, float], x: int, y: int) -> unreal.MaterialExpression:
    n = node(mat, unreal.MaterialExpressionConstant3Vector, x, y)
    n.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
    return n


def link(a, b, to_input: str, from_output: str = "") -> None:
    MEL.connect_material_expressions(a, from_output, b, to_input)


def to_property(expr, prop: unreal.MaterialProperty, from_output: str = "") -> None:
    MEL.connect_material_property(expr, from_output, prop)


def world_z(mat, x: int, y: int) -> unreal.MaterialExpression:
    """Absolute world-space Z (cm). Works identically on a Landscape and on the
    static-mesh fallback — the terrain material never reads painted weightmaps."""
    wp = node(mat, unreal.MaterialExpressionWorldPosition, x, y)
    mask = node(mat, unreal.MaterialExpressionComponentMask, x + 160, y)
    mask.set_editor_property("r", False)
    mask.set_editor_property("g", False)
    mask.set_editor_property("b", True)
    mask.set_editor_property("a", False)
    link(wp, mask, "")
    return mask


def linstep(mat, lo: float, hi: float, value_expr, x: int, y: int) -> unreal.MaterialExpression:
    """saturate((value - lo)/(hi - lo)) — a cheap smooth band edge."""
    sub = node(mat, unreal.MaterialExpressionSubtract, x, y)
    link(value_expr, sub, "A")
    sub.set_editor_property("const_b", float(lo))
    div = node(mat, unreal.MaterialExpressionDivide, x + 160, y)
    link(sub, div, "A")
    div.set_editor_property("const_b", float(max(hi - lo, 1e-3)))
    sat = node(mat, unreal.MaterialExpressionSaturate, x + 320, y)
    link(div, sat, "")
    return sat


def lerp(mat, a, b, alpha, x: int, y: int) -> unreal.MaterialExpression:
    n = node(mat, unreal.MaterialExpressionLinearInterpolate, x, y)
    link(a, n, "A")
    link(b, n, "B")
    link(alpha, n, "Alpha")
    return n


def recompile(mat) -> None:
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
