"""growth.py: growing provinces outward from tiered city points.

Uses the real "cities"/"provinces" layers init_package.py declares.
Cities is point_coupling=free with a "tier" field. Provinces is the
province_layer. map.json's city_layer/province_layer fields link the
two, not their names.
"""

import export
import growth
import mapfmt
import numpy as np
import pytest
from tests.conftest import land_rect, make_package

LAND_BOX = (10, 8, 50, 32)  # matches conftest's default backdrop


def _project(package):
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(LAND_BOX)]}
    ]
    return project


def _city(x, y, tier):
    return {"x": float(x), "y": float(y), "tier": tier}


@pytest.fixture
def package(tmp_path):
    return make_package(tmp_path, size=(60, 40), land_box=LAND_BOX)


def test_start_requires_at_least_one_city(package):
    project = _project(package)
    with pytest.raises(growth.GrowthError, match="no points"):
        growth.start(package, project)


def test_start_rejects_a_city_off_land(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(5, 5, 1)}  # in the sea
    with pytest.raises(growth.GrowthError, match="isn't on"):
        growth.start(package, project)


def test_start_seeds_one_tiny_province_per_city(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(20, 20, 3),
        "p2": _city(35, 20, 3),
    }

    result = growth.start(package, project)

    assert result["province_count"] == 2
    ids = sorted(f["id"] for f in result["features"])
    assert ids == [1, 2]
    assert result["growth"]["step"] == 0
    assert result["growth"]["seed_of"] == {"1": "p1", "2": "p2"}
    assert project["layers"]["provinces"]["features"] == result["features"]


def test_step_before_start_is_rejected(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 3)}
    with pytest.raises(growth.GrowthError, match="Start Over"):
        growth.step(package, project)


def test_step_grows_a_province_and_advances_the_counter(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 3)}
    growth.start(package, project)

    before_area = _province_area(package, project, 1)
    result = growth.step(package, project)
    after_area = _province_area(package, project, 1)

    assert result["step"] == 1
    assert result["changed_px"] > 0
    assert after_area > before_area
    assert "p1" in result["growing_cities"]


def test_higher_tier_grows_faster_than_lower_tier(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(20, 20, 1),
        "p2": _city(35, 20, 5),
    }
    growth.start(package, project)
    growth.step(package, project)

    slow_area = _province_area(package, project, 1)
    fast_area = _province_area(package, project, 2)
    assert fast_area > slow_area


def test_growth_never_claims_off_the_land_mask(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(20, 20, 5),
        "p2": _city(35, 20, 5),
    }
    growth.start(package, project)
    mask = growth._land_mask(package, project)

    for _ in range(6):
        growth.step(package, project)

    province_cfg = package.province_layer
    raster = export.rasterize_polygon_layer(project, province_cfg, package.size)
    claimed = export.id_buffer(raster) > 0
    assert not np.any(claimed & ~mask)


def test_growth_settles_and_reports_done(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 5)}
    growth.start(package, project)

    result = None
    for _ in range(20):
        result = growth.step(package, project)
        if result["done"]:
            break

    assert result["done"]
    assert result["changed_px"] == 0


def test_start_over_resets_a_grown_province(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 5)}
    growth.start(package, project)
    growth.step(package, project)
    grown_area = _province_area(package, project, 1)

    growth.start(package, project)
    reset_area = _province_area(package, project, 1)

    assert project["layers"]["provinces"]["growth"]["step"] == 0
    assert reset_area < grown_area


def _province_area(package, project, province_id):
    province_cfg = package.province_layer
    raster = export.rasterize_polygon_layer(project, province_cfg, package.size)
    return int((export.id_buffer(raster) == province_id).sum())
