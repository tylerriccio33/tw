"""Resources as landmark points, like cities.

Resources used to be a hand-painted brush region. They're now a
point_coupling=free layer whose "kind" field uses the "category"
point-field type. A "category" value must be one of the layer's own legend
keys. It colors the dot the same way cities' "tier" or army starts'
"faction" does. Nothing in the pipeline names "resources" or "category"
specially. This exercises the generic machinery through the resource layer
shape init_package now ships.
"""

import export
import mapfmt
import pytest
from tests.conftest import box, make_package, project_with

# The shape init_package.py now ships for resources: a free point layer
# colored by a category field picking from the legend.
RESOURCES_LAYER = {
    "name": "resources",
    "title": "Resources",
    "input": "point",
    "kind": "class",
    "raster": "resources.png",
    "nodata_color": "#000000",
    "clip_to": "coastline:land",
    "point_coupling": "free",
    "point_fields": {"kind": {"type": "category"}},
    "legend": {
        "#9aa0a6": {"key": "iron", "name": "Iron"},
        "#6b8fa3": {"key": "tin", "name": "Tin"},
        "#c9b79c": {"key": "wool", "name": "Wool"},
    },
}


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def _package(tmp_path):
    # make_package already includes a "resources" layer; override it with
    # the point-shaped one under test.
    return make_package(tmp_path, extra_layers={"resources": RESOURCES_LAYER})


def test_category_field_type_parses(tmp_path):
    package = _package(tmp_path)
    cfg = package.layers["resources"]
    assert cfg.input == "point"
    assert cfg.point_coupling == "free"
    assert cfg.point_fields["kind"]["type"] == "category"


def test_category_layer_without_a_legend_is_rejected():
    # A category field picks its value from the layer's legend, so a
    # legend-less one is meaningless. kind=class already enforces this
    # ("its colors mean nothing"); confirm the category shape can't slip
    # past it.
    with pytest.raises(mapfmt.PackageError, match="no legend"):
        mapfmt.parse_layer_config(
            {
                "name": "bad",
                "input": "point",
                "kind": "class",
                "point_coupling": "free",
                "point_fields": {"kind": {"type": "category"}},
                "legend": {},
            }
        )


def test_resource_points_export_to_points_json(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["resources"]["points"] = {
        "r1": {"x": 15.0, "y": 20.0, "kind": "tin"},
        "r2": {"x": 40.0, "y": 20.0, "kind": "wool"},
    }

    export.export_package(project, package)

    data = mapfmt.read_points_file(package.root)
    assert data["resources"] == [
        {"id": "r1", "x": 15.0, "y": 20.0, "kind": "tin"},
        {"id": "r2", "x": 40.0, "y": 20.0, "kind": "wool"},
    ]


def test_resource_dot_is_colored_by_its_kind(tmp_path):
    package = _package(tmp_path)
    cfg = package.layers["resources"]
    project = project_with(package, provinces=two_provinces())
    project["layers"]["resources"]["points"] = {
        "r1": {"x": 15.0, "y": 20.0, "kind": "tin"},
    }

    raster = export.rasterize_layer(project, package, cfg, package.size, None)
    tin_rgb = mapfmt.hex_to_rgb(cfg.color_for_key("tin"))
    assert tuple(raster[20, 15]) == tin_rgb


def test_a_kind_outside_the_legend_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["resources"]["points"] = {
        "r1": {"x": 15.0, "y": 20.0, "kind": "unobtanium"},
    }

    problems = export.validate_package(project, package)
    assert any("unobtanium" in p for p in problems)


def test_resource_point_off_land_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["resources"]["points"] = {
        "r1": {"x": 2.0, "y": 2.0, "kind": "iron"},  # open sea
    }

    problems = export.validate_package(project, package)
    assert any("not on" in p for p in problems)
