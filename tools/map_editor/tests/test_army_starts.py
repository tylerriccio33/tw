"""Army-start points: a point_coupling=free layer, config only.

Mirrors test_extensibility.py's roads/climate layers - `army_starts`
names no source file. It is a generic free point/class layer.
Its `point_fields` schema happens to describe a faction owner and a
unit composition. The export/validate machinery only knows the words
"faction" and "counts", never "army".
"""

import export
import mapfmt
import pytest
from tests.conftest import box, make_package, project_with

ARMY_STARTS_LAYER = {
    "name": "army_starts",
    "title": "Army Starts",
    "input": "point",
    "kind": "class",
    "raster": "army_starts.png",
    "nodata_color": "#000000",
    "clip_to": "coastline:land",
    "point_coupling": "free",
    "point_fields": {
        "owner": {"type": "faction"},
        "composition": {
            "type": "counts",
            "keys": ["archers", "melee", "cavalry"],
            "min": 0,
        },
    },
    "legend": {
        "#cd5c5c": {"key": "red", "name": "Red"},
        "#6495ed": {"key": "blue", "name": "Blue"},
        "#3cb371": {"key": "green", "name": "Green"},
        "#daa520": {"key": "yellow", "name": "Yellow"},
    },
}


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def _package(tmp_path):
    return make_package(tmp_path, extra_layers={"army_starts": ARMY_STARTS_LAYER})


def _valid_point():
    return {
        "x": 15.0,
        "y": 15.0,
        "owner": "red",
        "composition": {"archers": 4, "melee": 6, "cavalry": 2},
    }


# --------------------------------------------------------------------------
# extensibility: no source file names "army_starts"
# --------------------------------------------------------------------------


def test_army_starts_layer_needs_no_core_changes(tmp_path):
    package = _package(tmp_path)
    assert "army_starts" in package.layer_order

    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {"p1": _valid_point()}

    result = export.export_package(project, package)
    assert package.raster_path("army_starts").is_file()
    assert "points" in result


# --------------------------------------------------------------------------
# round trip through project.json
# --------------------------------------------------------------------------


def test_point_round_trips_through_project_json(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {"p1": _valid_point()}

    mapfmt.save_project(package.root, project)
    reloaded = mapfmt.load_project(package.root, package)

    assert reloaded["layers"]["army_starts"]["points"]["p1"] == _valid_point()


# --------------------------------------------------------------------------
# export shape
# --------------------------------------------------------------------------


def test_export_produces_points_json_with_expected_shape(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {
        "p1": _valid_point(),
        "p2": {
            "x": 35.0,
            "y": 20.0,
            "owner": "blue",
            "composition": {"archers": 0, "melee": 10, "cavalry": 0},
        },
    }

    export.export_package(project, package)

    data = mapfmt.read_points_file(package.root)
    assert data["army_starts"] == [
        {
            "id": "p1",
            "x": 15.0,
            "y": 15.0,
            "owner": "red",
            "composition": {"archers": 4, "melee": 6, "cavalry": 2},
        },
        {
            "id": "p2",
            "x": 35.0,
            "y": 20.0,
            "owner": "blue",
            "composition": {"archers": 0, "melee": 10, "cavalry": 0},
        },
    ]


def test_layer_with_no_free_points_still_gets_an_empty_entry(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())

    export.export_package(project, package)

    data = mapfmt.read_points_file(package.root)
    assert data["army_starts"] == []


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def test_unknown_owner_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {
        "p1": {**_valid_point(), "owner": "purple"}
    }

    problems = export.validate_package(project, package)
    assert any("purple" in p for p in problems)


def test_point_outside_map_bounds_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {"p1": {**_valid_point(), "x": 9999.0}}

    problems = export.validate_package(project, package)
    assert any("outside" in p for p in problems)


def test_point_off_land_is_reported(tmp_path):
    """army_starts clips to coastline:land, so validation rejects a point
    sitting in open sea (outside the traced land box)."""
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    project["layers"]["army_starts"]["points"] = {
        "p1": {**_valid_point(), "x": 2.0, "y": 2.0}
    }

    problems = export.validate_package(project, package)
    assert any("not on" in p for p in problems)


def test_negative_unit_count_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    point = _valid_point()
    point["composition"]["archers"] = -3
    project["layers"]["army_starts"]["points"] = {"p1": point}

    problems = export.validate_package(project, package)
    assert any("below the minimum" in p for p in problems)


def test_unknown_composition_key_is_reported(tmp_path):
    package = _package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    point = _valid_point()
    point["composition"]["siege"] = 1
    project["layers"]["army_starts"]["points"] = {"p1": point}

    problems = export.validate_package(project, package)
    assert any("unknown key" in p for p in problems)


# --------------------------------------------------------------------------
# layer config guardrails
# --------------------------------------------------------------------------


def test_free_point_layer_must_be_kind_class(tmp_path):
    bad = dict(ARMY_STARTS_LAYER, kind="identity", legend={})
    with pytest.raises(mapfmt.PackageError, match="point_coupling=free"):
        make_package(tmp_path, extra_layers={"army_starts": bad})


def test_province_point_layer_rejects_point_fields(tmp_path):
    bad = dict(ARMY_STARTS_LAYER, point_coupling="province", kind="identity", legend={})
    with pytest.raises(mapfmt.PackageError, match="point_coupling=province"):
        make_package(tmp_path, extra_layers={"army_starts": bad})
