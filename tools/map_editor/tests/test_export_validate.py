"""The validate_package checks export_package refuses to skip.

Each _validate_* helper catches one way authored data could lie about
what ends up in the game. Bad geometry, colliding ids, unknown keys,
overlapping provinces, and point layers out of sync with the province
roster.
"""

import export
import mapfmt
import numpy as np
import pytest
from PIL import Image
from tests.conftest import box, project_with


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def test_a_polygon_with_fewer_than_three_points_is_silently_skipped(package):
    """Not flagged as a problem - just not drawn. A 2-point 'polygon' is
    typically a leftover from an in-progress trace."""
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [[[10, 8], [30, 8]]]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_geometry(project, package)
    assert problems == []


def test_a_point_outside_the_map_is_flagged(package):
    provinces = [
        {
            "id": 1,
            "key": "west",
            "name": "West",
            "polygons": [box(10, 8, 30, 32) + [[9999, 9999]]],
        },
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_geometry(project, package)
    assert any("outside the" in p for p in problems)


def test_a_polygon_that_revisits_a_point_is_flagged(package):
    torn = [[10, 8], [30, 8], [10, 8], [30, 32]]
    provinces = [{"id": 1, "key": "west", "name": "West", "polygons": [torn]}]
    project = project_with(package, provinces=provinces)
    problems = export._validate_geometry(project, package)
    assert any("revisits the same point" in p for p in problems)


def test_a_self_intersecting_polygon_is_flagged(package):
    bowtie = [[10, 8], [30, 32], [30, 8], [10, 32]]
    provinces = [{"id": 1, "key": "west", "name": "West", "polygons": [bowtie]}]
    project = project_with(package, provinces=provinces)
    problems = export._validate_geometry(project, package)
    assert any("crosses itself" in p for p in problems)


def test_problem_polygons_skips_polygons_with_fewer_than_three_points(package):
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [[[10, 8], [30, 8]]]},
    ]
    project = project_with(package, provinces=provinces)
    assert export.problem_polygons(project, package) == []


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_a_province_with_no_id_is_flagged(package):
    project = project_with(package, provinces=two_provinces())
    del project["layers"]["provinces"]["features"][0]["id"]
    problems = export._validate_identity(project, package)
    assert any("has no id" in p for p in problems)


def test_a_province_id_of_zero_is_flagged(package):
    provinces = [
        {"id": 0, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]}
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_identity(project, package)
    assert any("ids start at 1" in p for p in problems)


def test_two_provinces_sharing_an_id_are_flagged(package):
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 25, 32)]},
        {"id": 1, "key": "east", "name": "East", "polygons": [box(25, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_identity(project, package)
    assert any("both use id 1" in p for p in problems)


def test_two_provinces_sharing_a_key_are_flagged(package):
    provinces = [
        {"id": 1, "key": "same", "name": "West", "polygons": [box(10, 8, 25, 32)]},
        {"id": 2, "key": "same", "name": "East", "polygons": [box(25, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_identity(project, package)
    assert any("both use key 'same'" in p for p in problems)


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------


def test_an_assignment_to_a_nonexistent_province_is_flagged(package):
    project = project_with(
        package, provinces=two_provinces(), assignments={"99": "red"}
    )
    problems = export._validate_keys(project, package)
    assert any("doesn't exist" in p for p in problems)


def test_an_assignment_with_an_unknown_faction_key_is_flagged(package):
    project = project_with(
        package, provinces=two_provinces(), assignments={"1": "not-a-faction"}
    )
    problems = export._validate_keys(project, package)
    assert any("isn't in that layer's legend" in p for p in problems)


# ---------------------------------------------------------------------------
# overlap
# ---------------------------------------------------------------------------


def test_two_provinces_overlapping_by_more_than_the_tolerance_are_flagged(package):
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 40, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(20, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_overlap(project, package)
    assert any("overlap by" in p for p in problems)


def test_a_province_wholly_enclosed_by_another_is_treated_as_an_enclave(package):
    """Full containment (an island province drawn after a larger one that
    surrounds it) is deliberate, not an overlap bug."""
    provinces = [
        {"id": 1, "key": "outer", "name": "Outer", "polygons": [box(10, 8, 50, 32)]},
        {"id": 2, "key": "inner", "name": "Inner", "polygons": [box(20, 15, 30, 25)]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_overlap(project, package)
    assert problems == []


def test_a_province_with_no_drawable_area_is_skipped_without_crashing(package):
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [[[10, 8], [10, 8]]]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=provinces)
    problems = export._validate_overlap(project, package)
    assert problems == []


# ---------------------------------------------------------------------------
# points
# ---------------------------------------------------------------------------


def test_a_point_keyed_by_a_non_integer_string_is_flagged(package):
    project = project_with(package, provinces=two_provinces())
    project["layers"]["cities"]["points"] = {"not-an-id": [20, 20]}
    problems = export._validate_points(project, package)
    assert any("not a province id" in p for p in problems)


def test_a_point_for_a_nonexistent_province_is_flagged(package):
    project = project_with(package, provinces=two_provinces())
    project["layers"]["cities"]["points"]["99"] = [20, 20]
    problems = export._validate_points(project, package)
    assert any("which doesn't exist" in p for p in problems)


def test_a_point_outside_the_map_bounds_is_flagged(package):
    project = project_with(package, provinces=two_provinces())
    project["layers"]["cities"]["points"]["1"] = [9999, 9999]
    problems = export._validate_points(project, package)
    assert any("is outside the" in p for p in problems)


def test_a_province_missing_its_point_is_flagged(package):
    project = project_with(package, provinces=two_provinces())
    del project["layers"]["cities"]["points"]["1"]
    problems = export._validate_points(project, package)
    assert any("no authored point for province 1" in p for p in problems)


# ---------------------------------------------------------------------------
# rasterize_layer: raster/map size mismatch
# ---------------------------------------------------------------------------


def test_a_brush_raster_whose_size_no_longer_matches_the_map_is_blocked(package):
    cfg = package.layers["terrain"]
    wrong_size = np.zeros((10, 10, 3), dtype=np.uint8)
    Image.fromarray(wrong_size).save(package.raster_path("terrain"))

    project = project_with(package, provinces=two_provinces())
    with pytest.raises(export.ExportBlocked, match="but the map is"):
        export.rasterize_layer(project, package, cfg, package.size, None)


# ---------------------------------------------------------------------------
# export_dir / export_package wiring
# ---------------------------------------------------------------------------


def test_export_dir_loads_the_package_from_disk_and_exports(package):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    result = export.export_dir(project, package.root)

    assert result["province_count"] == 2
    assert (package.root / mapfmt.TABLE_NAME).is_file()


def test_export_package_refuses_a_province_left_with_no_pixels_after_clipping(package):
    """A province drawn entirely over open sea has nothing left once
    clipped to the coastline mask. Export must refuse this instead of
    silently writing a province with no land."""
    provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "sea-only", "name": "SeaOnly", "polygons": [box(0, 0, 5, 5)]},
    ]
    project = project_with(package, provinces=provinces)

    with pytest.raises(export.ExportBlocked, match="no pixels left after clipping"):
        export.export_package(project, package)
