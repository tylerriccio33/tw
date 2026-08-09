"""The format's central claim, as a test.

Another kind of map information should take three things. A PNG, a JSON
legend, and one entry in map.json's layer list. Nothing in export.py,
mapfmt.py, the editor, or the game should change.

These tests add layers that no source file names, then assert they flow
through to the province table.

If someone special-cases a layer by name anywhere in the pipeline, this
is what breaks.
"""

import mapfmt
import numpy as np
import pytest
from PIL import Image
from tests.conftest import box, make_package, paint, project_with

LAND = (10, 8, 50, 32)

TRADE_ROUTES_LAYER = {
    "name": "trade_routes",
    "title": "Trade Routes",
    "input": "brush",
    "kind": "class",
    "raster": "trade_routes.png",
    "nodata_color": "#000000",
    "default_key": "dirt",
    "snap_source": False,
    "clip_to": "coastline:land",
    "legend": {
        "#8b5a2b": {"key": "dirt", "name": "Dirt road", "move_cost": 0.8},
        "#cfcfcf": {"key": "paved", "name": "Paved road", "move_cost": 0.5},
    },
    "reduce": {"into": "trade_routes", "mode": "any"},
}

CLIMATE_LAYER = {
    "name": "climate",
    "title": "Climate",
    "input": "brush",
    "kind": "class",
    "raster": "climate.png",
    "nodata_color": "#000000",
    "default_key": "temperate",
    "snap_source": False,
    "clip_to": "coastline:land",
    "legend": {
        "#4a7fb5": {"key": "arctic", "name": "Arctic"},
        "#7fb54a": {"key": "temperate", "name": "Temperate"},
    },
    "reduce": {"into": "climate", "mode": "majority"},
}


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def test_a_trade_routes_layer_needs_no_core_changes(tmp_path):
    """The acceptance test. `trade_routes` appears in no source file, only
    in the config here. It still gets rasterized, clipped, and reduced
    into every province's tags."""
    import export

    package = make_package(tmp_path, extra_layers={"trade_routes": TRADE_ROUTES_LAYER})
    assert "trade_routes" in package.layer_order

    project = project_with(package, provinces=two_provinces())
    paint(package, "trade_routes", "#8b5a2b", (12, 10, 28, 30))

    export.export_package(project, package)

    assert package.raster_path("trade_routes").is_file()
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["tags"]["trade_routes"] == ["dirt"]
    assert table[2]["tags"]["trade_routes"] == []


def test_two_new_layers_coexist_without_interfering(tmp_path):
    import export

    package = make_package(
        tmp_path,
        extra_layers={"trade_routes": TRADE_ROUTES_LAYER, "climate": CLIMATE_LAYER},
    )
    project = project_with(package, provinces=two_provinces())
    paint(package, "trade_routes", "#cfcfcf", (12, 10, 28, 30))
    paint(package, "climate", "#7fb54a", LAND)
    paint(package, "climate", "#4a7fb5", (10, 8, 26, 32))

    export.export_package(project, package)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["tags"] == {
        "terrain": "plains",  # nothing painted -> the layer's default_key
        "resources": [],
        "trade_routes": ["paved"],
        "climate": "arctic",
    }
    assert table[2]["tags"]["climate"] == "temperate"
    assert table[2]["tags"]["trade_routes"] == []


def test_a_new_layer_is_clipped_by_the_coastline_like_any_other(tmp_path):
    import export

    package = make_package(tmp_path, extra_layers={"trade_routes": TRADE_ROUTES_LAYER})
    project = project_with(package, provinces=two_provinces())
    paint(package, "trade_routes", "#8b5a2b", (0, 0, 60, 40))  # painted over everything

    export.export_package(project, package)

    with Image.open(package.raster_path("trade_routes")) as im:
        raster = np.array(im.convert("RGB"))
    assert tuple(raster[2, 2]) == (0, 0, 0), "road painted on open sea is clipped"
    assert tuple(raster[20, 20]) == mapfmt.hex_to_rgb("#8b5a2b")


def test_a_layer_added_after_a_project_was_saved_loads_as_empty(tmp_path):
    """Dropping a layer into an existing package shouldn't invalidate the
    project someone is midway through editing."""
    package = make_package(tmp_path, extra_layers={"trade_routes": TRADE_ROUTES_LAYER})
    project = project_with(package, provinces=two_provinces())
    del project["layers"]["trade_routes"]

    mapfmt.save_project(package.root, project)
    reloaded = mapfmt.load_project(package.root, package)

    assert "trade_routes" in reloaded["layers"]


# ---------------------------------------------------------------------------
# the guardrails that keep a malformed layer from failing silently
# ---------------------------------------------------------------------------


def test_a_layer_clipping_to_a_mask_defined_after_it_is_rejected(tmp_path):
    """Masks come from earlier layers. Referring forward would blow up
    mid-export with a KeyError, after some rasters were already written."""
    late = dict(TRADE_ROUTES_LAYER, clip_to="nowhere:land")
    with pytest.raises(mapfmt.PackageError, match="clip_to"):
        make_package(tmp_path, extra_layers={"trade_routes": late})


def test_a_layer_with_no_legend_is_rejected(tmp_path):
    nonsense = dict(TRADE_ROUTES_LAYER, legend={})
    with pytest.raises(mapfmt.PackageError, match="legend"):
        make_package(tmp_path, extra_layers={"trade_routes": nonsense})


def test_an_unknown_input_mode_is_rejected(tmp_path):
    nonsense = dict(TRADE_ROUTES_LAYER, input="telepathy")
    with pytest.raises(mapfmt.PackageError, match="input"):
        make_package(tmp_path, extra_layers={"trade_routes": nonsense})


def test_a_feature_naming_a_key_outside_its_legend_is_reported(tmp_path):
    import export

    package = make_package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["coastline"]["features"].append(
        {"key": "swamp", "polygons": [box(12, 10, 20, 20)]}
    )

    problems = export.validate_package(project, package)
    assert any("swamp" in p for p in problems)
