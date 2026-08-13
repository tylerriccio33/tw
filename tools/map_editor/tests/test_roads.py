"""roads.py: the traffic-simulation road network.

Uses the real "cities"/"roads" layers init_package.py declares.
map.json's road_layer links "roads" the same way city_layer/
province_layer link theirs. Nothing here names a layer directly -
tests only go through those pointers, mirroring growth.py's own tests.

start()/step() reveal a precomputed, cost-sorted trip list one
ceiling raise at a time. Most tests here only care about the
finished network. So _run_to_completion() steps until done, then
reshapes the result to look like a one-shot build() call would have."""

import mapfmt
import numpy as np
import pytest
import roads
from PIL import Image
from tests.conftest import land_rect, make_package, paint

LAND_BOX = (10, 8, 50, 32)  # matches conftest's default backdrop


def _project(package):
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(LAND_BOX)]}
    ]
    return project


def _city(x, y, tier=1):
    return {"x": float(x), "y": float(y), "tier": tier, "name": f"city-{x}-{y}"}


@pytest.fixture
def package(tmp_path):
    return make_package(tmp_path, size=(60, 40), land_box=LAND_BOX)


def _run_to_completion(package, project) -> dict:
    start_result = roads.start(package, project)
    result = start_result
    steps = 0
    while not result.get("done", False):
        result = roads.step(package, project)
        steps += 1
        assert steps < 1000, "step() never finished - budget/ceiling bug?"
    return {
        "cities": start_result["cities"],
        "resource_targets": start_result["resource_targets"],
        "completed_trips": result["painted_trips"],
        "discarded_trips": start_result["discarded_trips"],
        "tier1_px": result["tier1_px"],
        "tier2_px": result["tier2_px"],
        "tier3_px": result["tier3_px"],
    }


def _full_traveled(package, project) -> dict:
    """Every trip painted with no ceiling: the raw traveled
    accumulator. For tests that want to inspect it directly, rather
    than through a written raster."""
    trips, mask, cities, resources_n, discarded, _reach = roads._collect_trips(
        package, project
    )
    traveled = np.zeros(mask.shape, dtype=np.float32)
    for _cost, weight, ys, xs, _costs, _tier in trips:
        traveled[ys, xs] += weight
    return {
        "traveled": np.minimum(traveled, roads.MAX_TRAVELED),
        "mask": mask,
        "cities": len(cities),
        "resource_targets": resources_n,
        "completed_trips": len(trips),
        "discarded_trips": discarded,
    }


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_start_requires_at_least_one_city(package):
    project = _project(package)
    with pytest.raises(roads.RoadsError, match="no points"):
        roads.start(package, project)


def test_start_rejects_a_city_off_land(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(5, 5)}  # in the sea
    with pytest.raises(roads.RoadsError, match="isn't on"):
        roads.start(package, project)


def test_step_before_start_is_rejected(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20)}
    with pytest.raises(roads.RoadsError, match="Start Over"):
        roads.step(package, project)


def test_no_road_layer_configured_is_reported(package):
    package.manifest.pop("road_layer")
    project = _project(package)
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20)}
    with pytest.raises(roads.RoadsError, match="road_layer"):
        roads.start(package, project)


# ---------------------------------------------------------------------------
# a completed trip
# ---------------------------------------------------------------------------


def test_two_close_cities_complete_a_road(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    result = _run_to_completion(package, project)

    assert result["cities"] == 2
    assert result["completed_trips"] == 1
    assert result["discarded_trips"] == 0
    assert result["tier1_px"] + result["tier2_px"] + result["tier3_px"] > 0


def test_the_finished_raster_uses_only_legend_colors(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    _run_to_completion(package, project)

    road_cfg = package.road_layer
    with Image.open(package.raster_path(road_cfg.name)) as im:
        raster = np.array(im.convert("RGB"))

    painted_colors = {tuple(c) for c in raster.reshape(-1, 3).tolist()}
    legend_colors = {mapfmt.hex_to_rgb(k) for k in road_cfg.legend}
    nodata = road_cfg.nodata_rgb
    # Every painted pixel is either nodata or one of the legend's exact
    # tier colors - never a stray or blended value.
    assert painted_colors <= legend_colors | {nodata}
    # And the path between the two cities actually got a color.
    assert tuple(raster[20, 22]) != nodata


# ---------------------------------------------------------------------------
# stepping: the ceiling-driven reveal itself
# ---------------------------------------------------------------------------


def test_start_seeds_a_blank_layer_and_a_session(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    result = roads.start(package, project)

    assert result["step"] == 0
    assert result["painted_trips"] == 0
    assert result["total_trips"] == 1
    assert not result["done"]
    assert project["layers"]["roads"]["build"]["total_trips"] == 1
    # A ceiling of 0 already reveals each trip's own source pixel (cost
    # 0), so the raster isn't purely blank - but nothing has committed
    # yet, so no tier color should appear, only the pending preview.
    assert result["tier1_px"] == result["tier2_px"] == result["tier3_px"] == 0
    assert len(result["frontier"]) == 1

    road_cfg = package.road_layer
    with Image.open(package.raster_path(road_cfg.name)) as im:
        raster = np.array(im.convert("RGB"))
    painted = {tuple(c) for c in raster.reshape(-1, 3).tolist()}
    assert painted <= {road_cfg.nodata_rgb, mapfmt.hex_to_rgb("#39c8ff")}


def test_a_step_reveals_a_trip_and_advances_the_counter(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    roads.start(package, project)

    result = roads.step(package, project)

    assert result["painted_this_step"] == 1
    assert result["painted_trips"] == 1
    assert result["done"]
    # One step revealed the only trip and the session ends - no extra
    # maturation rounds, so the counter stops at the reveal step.
    assert result["step"] == 1


def test_a_farther_route_takes_more_steps_to_reveal_than_a_close_one(tmp_path):
    # Trip cost is roughly pixel distance on plain terrain, and step()
    # only reveals a trip once the ceiling (raised by STEP_COST_BUDGET
    # each call) reaches its cost - so a route several budgets away
    # shouldn't appear on step 1 the way an adjacent one does.
    land_box = (5, 5, 95, 35)
    package = make_package(tmp_path, size=(100, 40), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 20),
        "p2": _city(25, 20),  # ~15px away - within one step's budget
        "p3": _city(90, 20),  # ~80px away - needs several steps
    }
    roads.start(package, project)

    first = roads.step(package, project)
    assert first["painted_this_step"] == 1  # only the near pair so far
    assert not first["done"]

    result = first
    while not result["done"]:
        result = roads.step(package, project)
    assert result["step"] > 1
    assert result["painted_trips"] == 3  # p1-p2, p1-p3, p2-p3 all eventually land


def test_an_unfinished_trip_grows_and_reports_a_frontier_marker(tmp_path):
    # A route several steps from completing should show up as a pending
    # preview trail that extends farther each step - "growing", not just
    # popping into existence once it lands - plus a frontier marker whose
    # x position advances toward the target and whose tier matches the
    # source city, so a viewer can see whose crew that is.
    # Distance chosen so the trip still needs 3 steps at budget 60 even
    # after tier 3's build-speed bonus shrinks its reveal cost (see
    # roads._build_speed) - otherwise the second-step assertions below
    # would land on an already-finished trip.
    land_box = (5, 5, 245, 35)
    package = make_package(tmp_path, size=(250, 40), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 20, tier=3),
        "p2": _city(200, 20, tier=1),  # ~190px away
    }
    roads.start(package, project)

    first = roads.step(package, project)
    assert not first["done"]
    assert len(first["frontier"]) == 1
    assert first["frontier"][0]["tier"] == 3  # the source city, p1
    first_x = first["frontier"][0]["x"]

    road_cfg = package.road_layer
    with Image.open(package.raster_path(road_cfg.name)) as im:
        first_pending_px = int(
            np.all(
                np.array(im.convert("RGB")) == mapfmt.hex_to_rgb("#39c8ff"), axis=-1
            ).sum()
        )
    assert first_pending_px > 0

    second = roads.step(package, project)
    assert not second["done"]
    assert second["frontier"][0]["x"] > first_x  # the tip moved toward p2

    with Image.open(package.raster_path(road_cfg.name)) as im:
        second_pending_px = int(
            np.all(
                np.array(im.convert("RGB")) == mapfmt.hex_to_rgb("#39c8ff"), axis=-1
            ).sum()
        )
    assert second_pending_px > first_pending_px  # the trail grew longer


def test_a_completed_network_stops_the_instant_the_last_trip_reveals(tmp_path):
    # Two cities far enough apart that revealing their one trip takes
    # several steps. The session ends the moment the last trip lands -
    # no extra maturation rounds - so the step counter equals exactly
    # the number of reveal steps and no key advertises extra rounds.
    land_box = (5, 5, 195, 35)
    package = make_package(tmp_path, size=(200, 40), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 20, tier=5),
        "p2": _city(150, 20, tier=5),  # ~140px, needs 3 steps at budget 60
    }
    roads.start(package, project)

    result = None
    steps_to_finish = 0
    while result is None or not result["done"]:
        result = roads.step(package, project)
        steps_to_finish += 1

    assert steps_to_finish > 1  # actually took several steps to reveal
    assert "matured_rounds" not in result
    assert result["step"] == steps_to_finish


def test_finishing_a_multi_route_network_stops_at_the_last_reveal(tmp_path):
    # Three cities whose pairwise distances need different numbers of
    # steps to reveal - the session ends on the step that finishes the
    # *last* trip, with no post-completion rounds tacked on.
    land_box = (5, 5, 195, 65)
    package = make_package(tmp_path, size=(200, 70), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 20, tier=2),
        "p2": _city(60, 20, tier=2),  # a short hop from p1
        "p3": _city(190, 50, tier=2),  # far from both
    }
    roads.start(package, project)

    result = None
    steps_to_finish = 0
    while result is None or not result["done"]:
        result = roads.step(package, project)
        steps_to_finish += 1

    assert steps_to_finish > 1  # actually took several steps to reveal it all
    assert result["painted_trips"] == result["total_trips"]
    assert "matured_rounds" not in result
    assert result["step"] == steps_to_finish


def test_start_over_resets_a_session_in_progress(package):
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    roads.start(package, project)
    roads.step(package, project)
    assert project["layers"]["roads"]["build"]["painted_trips"] == 1

    result = roads.start(package, project)
    assert result["step"] == 0
    assert result["painted_trips"] == 0
    assert result["tier1_px"] == result["tier2_px"] == result["tier3_px"] == 0

    road_cfg = package.road_layer
    with Image.open(package.raster_path(road_cfg.name)) as im:
        raster = np.array(im.convert("RGB"))
    painted = {tuple(c) for c in raster.reshape(-1, 3).tolist()}
    assert painted <= {road_cfg.nodata_rgb, mapfmt.hex_to_rgb("#39c8ff")}


def test_bounded_dijkstra_handles_a_source_with_no_land_neighbor(package):
    # A completely isolated single-pixel speck: the vectorized edge
    # builder must not crash trying to concatenate zero edge arrays.
    mask = np.zeros((5, 5), dtype=bool)
    mask[2, 2] = True
    cost = np.ones((5, 5), dtype=np.float32)

    dist, _pred, _y0, _x0 = roads._bounded_dijkstra(cost, mask, 2, 2, 100.0, 2)

    assert dist[2, 2] == 0.0
    assert np.isinf(dist).sum() == dist.size - 1


def test_step_survives_a_cold_cache(package):
    # A fresh server process has no module-level trip cache, only what
    # start() wrote into project.json - step() has to recompute and
    # fast-forward to the recorded ceiling rather than erroring.
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    roads.start(package, project)
    roads._trip_cache.clear()

    result = roads.step(package, project)

    assert result["painted_this_step"] == 1
    assert result["done"]


def test_step_survives_a_cold_cache_after_completion(package):
    # A restart after the network already finished has to replay the
    # reveal when rebuilding from a cold cache and reproduce the same
    # painted tiers - a step() right after a reload must not lose or
    # double-count any traffic.
    project = _project(package)
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20),
        "p2": _city(30, 20),
    }
    roads.start(package, project)
    finished = roads.step(package, project)
    assert finished["done"]

    roads._trip_cache.clear()
    result = roads.step(package, project)

    assert result["done"]
    assert result["tier1_px"] + result["tier2_px"] + result["tier3_px"] == (
        finished["tier1_px"] + finished["tier2_px"] + finished["tier3_px"]
    )


# ---------------------------------------------------------------------------
# reach: a target beyond the search budget gets no road at all
# ---------------------------------------------------------------------------


BIG_LAND_BOX = (10, 8, 690, 52)


@pytest.fixture
def big_package(tmp_path):
    return make_package(tmp_path, size=(700, 60), land_box=BIG_LAND_BOX)


def test_cities_far_beyond_the_search_budget_get_no_road(big_package):
    project = mapfmt.empty_project(big_package.size, big_package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(BIG_LAND_BOX)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(15, 30),
        "p2": _city(685, 30),  # ~670px of plain terrain, budget is 500
    }
    result = _run_to_completion(big_package, project)

    assert result["completed_trips"] == 0
    assert result["discarded_trips"] == 1
    assert result["tier1_px"] == result["tier2_px"] == result["tier3_px"] == 0


def test_mountains_shrink_reach_even_when_pixel_distance_is_short(tmp_path):
    # Two cities only 80px apart - trivially within budget on plain
    # terrain (cost ~80) - but a thick mountain band sits directly
    # between them. Move cost is a global table keyed by terrain key
    # name (growth.TERRAIN_MOVE_COST), so painting "mountains" makes it
    # ~20x more expensive to cross: 30px of it alone costs 600, over the
    # 500 budget even before the plain on either side. Confirms reach is
    # a cost-distance, not a pixel radius.
    land_box = (5, 5, 95, 55)
    package = make_package(tmp_path, size=(100, 60), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    mountains = package.layers["terrain"].color_for_key("mountains")
    paint(package, "terrain", mountains, (40, 5, 70, 55))
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 30),
        "p2": _city(90, 30),
    }

    result = _run_to_completion(package, project)

    assert result["completed_trips"] == 0
    assert result["discarded_trips"] == 1


# ---------------------------------------------------------------------------
# resource attraction
# ---------------------------------------------------------------------------


def test_a_resource_tile_pulls_a_road_from_a_city(package):
    project = _project(package)
    # A resource landmark east of the city - one dot, one road target.
    project["layers"]["resources"]["points"] = {
        "r1": {"x": 43, "y": 20, "kind": "iron"}
    }
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20)}

    result = _run_to_completion(package, project)

    assert result["resource_targets"] == 1
    assert result["completed_trips"] == 1
    # Which tier it lands on depends on the accumulated traffic weight -
    # here just confirm a road actually got painted at all.
    assert result["tier1_px"] + result["tier2_px"] + result["tier3_px"] > 0


def test_a_class_layer_with_unrecognized_keys_never_pulls_roads(tmp_path):
    # Mirrors test_growth.py's equivalent - ATTRACTION_KEYS is a global
    # table, not something any map package can extend by painting a
    # class layer with unrelated key names.
    climate_layer = {
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
    package = make_package(tmp_path, extra_layers={"climate": climate_layer})
    project = _project(package)
    paint(package, "climate", "#4a7fb5", (40, 18, 46, 22))
    project["layers"]["cities"]["points"] = {"p1": _city(15, 20)}

    result = _run_to_completion(package, project)

    assert result["resource_targets"] == 0
    assert result["completed_trips"] == 0
    assert result["discarded_trips"] == 0


# ---------------------------------------------------------------------------
# traffic weight: tier and stacking
# ---------------------------------------------------------------------------


def test_higher_tier_cities_generate_more_traffic_at_the_same_distance(tmp_path):
    low = make_package(tmp_path / "low", size=(60, 40), land_box=LAND_BOX)
    high = make_package(tmp_path / "high", size=(60, 40), land_box=LAND_BOX)

    low_project = _project(low)
    low_project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20, tier=1),
        "p2": _city(30, 20, tier=1),
    }
    high_project = _project(high)
    high_project["layers"]["cities"]["points"] = {
        "p1": _city(15, 20, tier=5),
        "p2": _city(30, 20, tier=5),
    }

    low_result = _full_traveled(low, low_project)
    high_result = _full_traveled(high, high_project)

    assert high_result["traveled"].sum() > low_result["traveled"].sum()


def test_a_shared_corridor_carries_more_traffic_than_a_lone_spur(tmp_path):
    # H --- T1 --- T2, collinear. T2 is a big (tier 5) city, so the
    # H-T2 route's path runs straight through the T1-T2 stretch too -
    # the same overlap that turns point-to-point routes into trunk
    # roads. The T1-T2 stretch should end up carrying both the T1-T2
    # pair's own traffic AND the H-T2 pair's, while the H-T1 stretch
    # only carries the (much smaller) H-T1 pair's traffic on its own.
    land_box = (5, 5, 55, 35)
    package = make_package(tmp_path, size=(60, 40), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": _city(10, 20, tier=1),  # H
        "p2": _city(30, 20, tier=1),  # T1
        "p3": _city(50, 20, tier=5),  # T2
    }

    result = _full_traveled(package, project)
    traveled = result["traveled"]

    h_t1_stretch = float(traveled[20, 15])  # between H and T1 only
    t1_t2_stretch = float(traveled[20, 40])  # between T1 and T2

    # Every pixel between H and T2 lies on the H-T2 path too, so both
    # stretches also pick up that pair's contribution on top of their
    # own local pair.
    h_t2_passthrough = roads._trips(1 * 5, 40.0)
    expected_h_t1 = roads._trips(1 * 1, 20.0) + h_t2_passthrough  # H-T1 pair
    expected_t1_t2 = roads._trips(1 * 5, 20.0) + h_t2_passthrough  # T1-T2 pair

    assert h_t1_stretch == pytest.approx(expected_h_t1, rel=1e-3)
    assert t1_t2_stretch == pytest.approx(expected_t1_t2, rel=1e-3)
    assert t1_t2_stretch > h_t1_stretch


def test_traveled_is_capped_at_max_traveled(tmp_path):
    # Enough high-tier cities clustered together that their combined
    # traffic on the shared pixels next to one of them would blow past
    # MAX_TRAVELED without the cap.
    land_box = (5, 5, 95, 35)
    package = make_package(tmp_path, size=(100, 40), land_box=land_box)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["cities"]["points"] = {
        f"p{i}": _city(10 + i * 12, 20, tier=5) for i in range(6)
    }

    result = _full_traveled(package, project)

    assert result["traveled"].max() <= roads.MAX_TRAVELED


# ---------------------------------------------------------------------------
# build speed: a bigger city's crew reveals its roads faster
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("distance", [80, 120, 160])
def test_a_bigger_city_reveals_the_same_route_in_fewer_steps(tmp_path, distance):
    # Same straight-line distance, same target, only the source city's
    # tier differs between the two scenarios - a tier-5 source should
    # never take more steps to finish its trip than a tier-1 source at
    # the identical distance, and for every distance tried here it
    # should take strictly fewer (its crew is faster, not just as fast).
    land_box = (5, 5, 195, 35)

    def steps_to_finish(tier):
        package = make_package(
            tmp_path / f"d{distance}-t{tier}", size=(200, 40), land_box=land_box
        )
        project = mapfmt.empty_project(package.size, package)
        project["layers"]["coastline"]["features"] = [
            {"key": "land", "polygons": [land_rect(land_box)]}
        ]
        project["layers"]["cities"]["points"] = {
            "p1": _city(10, 20, tier=tier),
            "p2": _city(10 + distance, 20, tier=1),
        }
        roads.start(package, project)
        result = None
        steps = 0
        while result is None or not result["done"]:
            result = roads.step(package, project)
            steps += 1
        return steps

    low_tier_steps = steps_to_finish(1)
    high_tier_steps = steps_to_finish(5)

    assert high_tier_steps < low_tier_steps


# ---------------------------------------------------------------------------
# consolidation: near-parallel roads merge onto one centerline
# ---------------------------------------------------------------------------


def test_consolidate_merges_two_parallel_roads_onto_one_line():
    # Two horizontal roads 3px apart, each carrying weight 5 - inside the
    # consolidation radius, so they're really one corridor. After
    # consolidation their traffic pools onto a single centerline: the
    # merged pixels carry ~10, the network occupies fewer distinct rows,
    # and the process neither creates nor loses any traffic.
    traveled = np.zeros((20, 40), dtype=np.float32)
    mask = np.ones((20, 40), dtype=bool)
    traveled[8, 5:35] = 5.0
    traveled[11, 5:35] = 5.0

    out = roads._consolidate(traveled, mask, roads.CONSOLIDATE_RADIUS_PX)

    assert out.sum() == pytest.approx(traveled.sum())  # traffic conserved
    assert out.max() > traveled.max()  # two roads' weight stacked onto one
    rows_before = np.count_nonzero(traveled.any(axis=1))
    rows_after = np.count_nonzero(out.any(axis=1))
    assert rows_after < rows_before  # collapsed toward a single centerline


def test_consolidate_leaves_a_lone_road_traffic_intact():
    # A single isolated road has nothing within radius to merge with, so
    # consolidation must not invent or drop any of its traffic.
    traveled = np.zeros((20, 40), dtype=np.float32)
    mask = np.ones((20, 40), dtype=bool)
    traveled[10, 5:35] = 3.0

    out = roads._consolidate(traveled, mask, roads.CONSOLIDATE_RADIUS_PX)

    assert out.sum() == pytest.approx(traveled.sum())


def test_consolidate_radius_zero_is_a_no_op():
    traveled = np.zeros((10, 10), dtype=np.float32)
    mask = np.ones((10, 10), dtype=bool)
    traveled[3, 2:8] = 2.0
    traveled[5, 2:8] = 2.0

    out = roads._consolidate(traveled, mask, 0)

    assert np.array_equal(out, traveled)


def test_build_speed_scales_with_tier():
    # Directly pins down the shape of the speed curve so a future change
    # to TIER_BUILD_SPEED_PER_LEVEL can't silently invert it: strictly
    # increasing, tier 1 is the 1x baseline.
    speeds = [roads._build_speed(tier) for tier in range(1, 6)]
    assert speeds[0] == 1.0
    assert speeds == sorted(speeds)
    assert len(set(speeds)) == len(speeds)
