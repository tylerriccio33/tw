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
from tests.conftest import land_rect, make_package, paint

LAND_BOX = (10, 8, 50, 32)  # matches conftest's default backdrop


def _project(package):
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(LAND_BOX)]}
    ]
    return project


def _city(x, y, tier):
    return {"x": float(x), "y": float(y), "tier": tier, "name": f"city-{x}-{y}"}


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


BIG_LAND_BOX = (10, 10, 590, 390)


@pytest.fixture
def big_package(tmp_path):
    return make_package(tmp_path, size=(600, 400), land_box=BIG_LAND_BOX)


def test_growth_produces_valid_geometry_after_many_steps(big_package):
    # Sanity check that a longer growth session on a plain rectangle of
    # land still validates cleanly. The real pinching regression this
    # guards against (see test_growth_realistic.py) needs a coastline
    # with obstacles to reproduce - a rectangle has nothing for two
    # claims to wrap around - but this still catches gross breakage.
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        f"p{i}": _city(x, y, tier)
        for i, (x, y, tier) in enumerate(
            [
                (100, 100, 1),
                (250, 100, 2),
                (400, 100, 3),
                (100, 250, 4),
                (250, 250, 5),
                (400, 250, 1),
                (175, 350, 2),
                (325, 350, 3),
            ],
            start=1,
        )
    }
    growth.start(package, project)

    for _ in range(200):
        result = growth.step(package, project)
        if result["done"]:
            break

    assert export.validate_package(project, package) == []


def test_growth_around_a_pinch_obstacle_produces_valid_geometry(pinch_package):
    # Fast regression for the same invariant test_growth_realistic.py
    # checks against the real backdrop (see conftest.pinch_package's
    # docstring for why this smaller coastline still reproduces it):
    # growth wrapping around an obstacle can leave a claim touching
    # itself at a single pixel corner, which cv2.findContours traces as
    # a self-intersecting polygon that export rejects.
    package, project = pinch_package
    growth.start(package, project)

    result = None
    for _ in range(400):
        result = growth.step(package, project)
        if result["done"]:
            break

    assert result["done"]
    assert export.validate_package(project, package) == []


def _province_area(package, project, province_id):
    province_cfg = package.province_layer
    raster = export.rasterize_polygon_layer(project, province_cfg, package.size)
    return int((export.id_buffer(raster) == province_id).sum())


def _claimed_area_within(package, project, province_id, region_mask):
    province_cfg = package.province_layer
    raster = export.rasterize_polygon_layer(project, province_cfg, package.size)
    claim = export.id_buffer(raster)
    return int(((claim == province_id) & region_mask).sum())


# ---------------------------------------------------------------------------
# sticky boundaries: gravity toward natural borders + the trim pass
# ---------------------------------------------------------------------------


def test_a_terrain_key_with_no_sticky_flag_never_pauses_growth(package):
    # Painting terrain that isn't in growth.STICKY_KEYS (mountains, forest,
    # river) must not change growth's behavior at all. Confirms the
    # mechanism only applies to those hardcoded keys, not "any terrain
    # slows growth".
    project = _project(package)
    hills = package.layers["terrain"].color_for_key("hills")
    paint(package, "terrain", hills, (25, 8, 50, 32))
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20, 1)}
    growth.start(package, project)

    assert growth._sticky_mask(package, project).sum() == 0
    result = growth.step(package, project)
    assert result["changed_px"] > 0
    assert "sticky_hold" not in result["growth"] or not result["growth"]["sticky_hold"]


def test_step_holds_at_a_sticky_boundary_before_punching_through(package):
    # A city right next to a painted mountain band: the front reaches the
    # mountains almost immediately (tier-1 radius covers the ~10px gap in
    # one step), but shouldn't claim any mountain pixel until it's touched
    # the boundary STICKY_HOLD_STEPS times in a row - growing normally
    # elsewhere in the meantime, never marked "finished" while paused.
    project = _project(package)
    mountains = package.layers["terrain"].color_for_key("mountains")
    paint(package, "terrain", mountains, (25, 8, 50, 32))
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20, 1)}
    growth.start(package, project)

    sticky = growth._sticky_mask(package, project)
    assert sticky.sum() > 0

    claimed_sticky_by_step = []
    for _ in range(growth.STICKY_HOLD_STEPS + 1):
        result = growth.step(package, project)
        assert "p1" not in result["finished_cities"]
        claimed_sticky_by_step.append(_claimed_area_within(package, project, 1, sticky))

    # No mountain pixel claimed for the first STICKY_HOLD_STEPS - 1 steps...
    assert all(n == 0 for n in claimed_sticky_by_step[: growth.STICKY_HOLD_STEPS - 1])
    # ...then the front breaks through.
    assert claimed_sticky_by_step[growth.STICKY_HOLD_STEPS - 1] > 0


def test_step_keeps_growing_elsewhere_while_paused_at_a_boundary(big_package):
    # The pause is local to the sticky front, not the whole province: a
    # city touching a mountain band on one side should still be able to
    # claim ordinary land elsewhere while held at the boundary. Uses the
    # big, open map so there's room left to grow into once the front
    # first reaches the mountains (a small map's open land runs out in
    # one step, which would mask this).
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    mountains = package.layers["terrain"].color_for_key("mountains")
    paint(package, "terrain", mountains, (150, 10, 590, 390))
    project["layers"]["cities"]["points"] = {"p1": _city(100, 200, 1)}
    growth.start(package, project)

    for _ in range(3):  # reach the boundary, first hold tick
        growth.step(package, project)
    growth_meta = project["layers"]["provinces"]["growth"]
    assert growth_meta["sticky_hold"].get("p1", 0) >= 1

    area_before = _province_area(package, project, 1)
    growth.step(package, project)  # still held, but land elsewhere is open
    area_after = _province_area(package, project, 1)

    assert growth_meta["sticky_hold"]["p1"] < growth.STICKY_HOLD_STEPS
    assert area_after > area_before


def test_trim_requires_a_grown_province(package):
    project = _project(package)
    with pytest.raises(growth.GrowthError, match="Start/Step"):
        growth.trim_to_sticky_boundaries(package, project)


def test_trim_is_a_noop_without_any_sticky_terrain(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 3)}
    growth.start(package, project)
    growth.step(package, project)

    result = growth.trim_to_sticky_boundaries(package, project)

    assert result["changed_px"] == 0


def test_trim_cuts_an_overshoot_back_toward_the_nearer_city(package):
    # A fast (tier 5) city and a slow (tier 1) city on opposite sides of a
    # mountain band race to grow into it - the fast city dominates the
    # contest and, once growth settles, has claimed land on the far side
    # of the boundary that's actually closer to the slow city. Trim should
    # pull some of that back.
    project = _project(package)
    mountains = package.layers["terrain"].color_for_key("mountains")
    paint(package, "terrain", mountains, (28, 8, 32, 32))
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20, 5),
        "p2": _city(45, 20, 1),
    }
    growth.start(package, project)
    result = None
    for _ in range(30):
        result = growth.step(package, project)
        if result["done"]:
            break
    assert result["done"]

    fast_area_before = _province_area(package, project, 1)
    slow_area_before = _province_area(package, project, 2)
    assert fast_area_before > slow_area_before  # confirms the overshoot happened

    trim_result = growth.trim_to_sticky_boundaries(package, project)

    assert trim_result["changed_px"] > 0
    slow_area_after = _province_area(package, project, 2)
    assert slow_area_after > slow_area_before
    assert export.validate_package(project, package) == []


def test_trim_is_idempotent(package):
    project = _project(package)
    mountains = package.layers["terrain"].color_for_key("mountains")
    paint(package, "terrain", mountains, (28, 8, 32, 32))
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20, 5),
        "p2": _city(45, 20, 1),
    }
    growth.start(package, project)
    result = None
    for _ in range(30):
        result = growth.step(package, project)
        if result["done"]:
            break

    growth.trim_to_sticky_boundaries(package, project)
    second = growth.trim_to_sticky_boundaries(package, project)

    assert second["changed_px"] == 0


# ---------------------------------------------------------------------------
# tier speed edge cases
# ---------------------------------------------------------------------------


def test_a_city_missing_the_tier_field_defaults_to_tier_one_speed(big_package):
    # tier_of() falls back to int(payload.get(tier_field, 1)) - a city
    # authored without a tier at all (rather than an explicit tier=1)
    # must grow at the same, slowest speed rather than erroring or
    # defaulting to something faster. Placed far apart so contesting the
    # other's pixels doesn't affect either city's single-step growth -
    # any area difference then comes only from the tier default, not
    # from which city gets processed first in a contested tie.
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": {"x": 150, "y": 200, "name": "no-tier"},
        "p2": _city(450, 200, 1),
    }
    growth.start(package, project)
    growth.step(package, project)

    untiered_area = _province_area(package, project, 1)
    explicit_tier_one_area = _province_area(package, project, 2)
    assert untiered_area == explicit_tier_one_area


def test_tier_speed_scales_linearly_with_pixels_per_tier_step(package):
    # radius = max(1, tier) * PIXELS_PER_TIER_STEP. Two cities far enough
    # apart that neither claim reaches the other (or the coastline) in one
    # step should each grow into a disc of radius tier * step size, so the
    # ratio of claimed areas after one step should track the square of the
    # ratio of tiers.
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(20, 20, 1),
        "p2": _city(35, 20, 2),
    }
    growth.start(package, project)
    growth.step(package, project)

    tier1_area = _province_area(package, project, 1)
    tier2_area = _province_area(package, project, 2)
    # Not an exact circle (clipped by land/step interactions), but a
    # tier-2 city's border moves twice as far, so its claimed area should
    # be well above the tier-1 city's, not merely equal or marginally more.
    assert tier2_area > tier1_area * 1.5


# ---------------------------------------------------------------------------
# restarting mid-growth
# ---------------------------------------------------------------------------


def test_start_over_with_a_different_city_set_replaces_the_previous_provinces(package):
    # start() wipes whatever the province layer currently holds, per its
    # docstring. Confirm that growing from a smaller city set, then
    # calling start() again with more cities, actually replaces (not
    # merges with) the previous province set.
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(20, 20, 3)}
    growth.start(package, project)
    growth.step(package, project)
    assert project["layers"]["provinces"]["growth"]["seed_of"] == {"1": "p1"}

    project["layers"]["cities"]["points"] = {
        "p1": _city(20, 20, 3),
        "p2": _city(35, 20, 3),
    }
    result = growth.start(package, project)

    assert result["province_count"] == 2
    assert project["layers"]["provinces"]["growth"]["step"] == 0
    assert project["layers"]["provinces"]["growth"]["seed_of"] == {
        "1": "p1",
        "2": "p2",
    }
    assert project["layers"]["provinces"]["growth"]["finished_cities"] == []


def test_step_survives_a_cold_cache_mid_growth(big_package):
    # Growth runs as a sequence of separate HTTP requests handled by a
    # process that may have restarted between them - step() then has to
    # fall back to rebuilding the claim buffer from the polygons on
    # record instead of the (now-missing) cached one. Clearing growth's
    # module-level caches simulates that, and growth should still be
    # able to make progress on the next step.
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(150, 200, 1),
        "p2": _city(450, 200, 2),
    }
    growth.start(package, project)
    growth.step(package, project)

    growth._claim_cache.clear()
    growth._land_mask_cache.clear()

    result = growth.step(package, project)

    assert result["changed_px"] > 0
    assert export.validate_package(project, package) == []


# ---------------------------------------------------------------------------
# multiple simultaneous growth fronts
# ---------------------------------------------------------------------------


def test_cities_of_equal_tier_split_the_land_close_to_evenly(big_package):
    # Two equal-tier cities, symmetric about the land rectangle's midline
    # and far enough apart that most of their growth happens before the
    # fronts meet, should claim close to equal shares once growth
    # finishes - the front forms roughly down the middle rather than one
    # city dominating just because a step processes it first.
    # (Cities right next to each other relative to the per-step growth
    # radius would let whichever a step processes first sweep nearly
    # everything - that's a real, order-dependent quirk of ties, not
    # what this test is after.)
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(200, 200, 1),
        "p2": _city(400, 200, 1),
    }
    growth.start(package, project)

    result = None
    for _ in range(60):
        result = growth.step(package, project)
        if result["done"]:
            break
    assert result["done"]

    area1 = _province_area(package, project, 1)
    area2 = _province_area(package, project, 2)
    total = area1 + area2
    assert abs(area1 - area2) / total < 0.15


def test_a_city_deleted_mid_growth_stops_its_province_cleanly(big_package):
    # step() treats a city_id missing from the current points as "someone
    # deleted this city" and just stops growing its province instead of
    # erroring - the province it already grew stays put. Uses a big,
    # sparsely-seeded map so the deleted city is still actively growing
    # (not already boxed in and finished on its own) at the moment it's
    # deleted - otherwise the "finished because deleted" and "finished
    # because boxed in" code paths would be indistinguishable.
    package = big_package
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(200, 200, 1),
        "p2": _city(400, 200, 1),
    }
    growth.start(package, project)
    growth.step(package, project)
    area_before = _province_area(package, project, 2)

    del project["layers"]["cities"]["points"]["p2"]
    result = growth.step(package, project)

    assert "p2" in result["finished_cities"]
    area_after = _province_area(package, project, 2)
    assert area_after == area_before
    assert export.validate_package(project, package) == []
