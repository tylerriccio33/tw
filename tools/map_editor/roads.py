"""Builds the roads layer: a traffic simulation between cities and
resources, not a hand-painted layer.

Every city sends traffic toward every other city and every resource
tile within reach. "Reach" is a cost-distance budget over the same
terrain move-cost table growth.py uses. Mountains are as hard to road
through as they are to grow through. A single bounded Dijkstra search
per city finds it. A target outside that budget never got a road: the
round trip never completed, so it leaves no trace. That's like a real
trade route nobody ever walked.

A target inside the budget contributes a "traffic weight" along every
pixel of its shortest path. The weight is bigger for a bigger source
city, and bigger for a richer target. That's the round-trip
abstraction collapsed into one weighted accumulation, instead of
literally animating trips back and forth. One round trip and a
hundred round trips differ only in how much weight lands on the path.
That's exactly what "more traveled" means here.

Two unrelated city/target pairs' cheapest paths often reuse the same
corridor: a mountain pass, a river valley. Their traffic weight then
stacks on the shared pixels automatically. That's what turns a
handful of point-to-point routes into a real road network's shape.
Recognizable trunks and spurs, with no extra logic needed.

start()/step() reveal that network the same ceiling-driven way
growth.py grows provinces. Unlike growth's shared frontier, a bounded
Dijkstra per city is cheap enough to run all up front. So every
completed trip gets precomputed once, then step() raises a cost
ceiling. A trip paints progressively as the ceiling passes each of
its own pixels' costs. It literally grows outward from its source
city toward its target, not just popping into existence once
finished. It only turns into a real, weighted, tiered road once the
ceiling reaches its target. Every still-growing trip also reports a
frontier marker: its current leading pixel. That's how the UI shows
each city's road-building progress.
"""

from __future__ import annotations

from collections import namedtuple

import cv2
import export
import growth
import mapfmt
import numpy as np
import scipy.ndimage as ndi
from PIL import Image
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra as sparse_dijkstra

# Cost-distance ceiling for a city's search. A target whose cheapest path
# costs more than this never gets a road - the "some radius" the design
# calls for, expressed in the same cost units as terrain move_cost rather
# than raw pixels, so mountains shrink a city's effective reach the same
# way they slow growth.
ROAD_SEARCH_BUDGET = 500.0

# Per-(source, target) traffic weight, before the 1/(1+cost) distance
# falloff: two tier-5 cities pull the most traffic, a resource always
# pulls whatever its own value implies regardless of the city's tier
# (see RESOURCE_TARGET_WEIGHT), and a spur to a middling target somewhere
# in between.
BASE_TRIP_SCALE = 1.0
COST_NORM = 120.0
MAX_TRIPS_PER_PAIR = 8.0

# The "some cap to how travelled a road can be" the design calls for -
# traffic keeps stacking past this, but the raster only records up to it.
MAX_TRAVELED = 40.0

# Traveled-weight thresholds that split the capped traffic into 3 tiers.
# Below TIER2_MIN is tier 1 (a spur used by one or two pairs); at or
# above TIER3_MIN is tier 3 (a trunk road several pairs share).
TIER2_MIN = 4.0
TIER3_MIN = 15.0

# How many pixels wide each tier paints, via a dilation on its own mask -
# purely a legibility aid so trunk roads read clearly at real map scale
# (thousands of px across) and trunk roads read thicker than trails.
TIER_WIDTH_PX = {1: 4, 2: 7, 3: 12}

# A trip that's still under construction (ceiling hasn't reached its
# target yet) previews as a thin trail in this width, distinct from any
# committed tier - see "road_pending" in the legend.
PENDING_WIDTH_PX = 3

# Two roads whose centerlines run within this many pixels of each other
# are really one corridor - the traffic sim just happens to have routed
# their cheapest paths a few pixels apart (a river valley one trip hugs
# the near bank of and another the far bank, say). Consolidation snaps
# such near-parallel roads onto a single shared centerline and adds their
# traffic together, so what would render as two thin trails becomes one
# busier - and therefore higher-tier, thicker - road. Set to 0 to disable.
CONSOLIDATE_RADIUS_PX = 6

# A resource's intrinsic pull as a road target, independent of any city's
# tier. Not every ATTRACTION_KEYS entry needs an explicit weight - an
# unlisted one just falls back to 1.0, same as growth.py's unlisted
# terrain falls back to move_cost 1.0.
RESOURCE_TARGET_WEIGHT: dict[str, float] = {
    "silver": 3.0,
    "tin": 2.5,
    "wine": 2.0,
    "wool": 2.0,
    "cloth": 2.0,
    "iron": 2.0,
    "coal": 1.5,
    "timber": 1.5,
    "lead": 1.5,
    "fish": 1.0,
    "salt": 1.0,
}

# A resource blob smaller than this is paint noise, not a real deposit
# worth routing a road to.
MIN_RESOURCE_BLOB_PX = 4

# Cost units the ceiling advances per step - same "shared budget" idea as
# growth.STEP_COST_BUDGET, just in road-trip cost units rather than
# per-pixel frontier cost. Trip costs run up to ROAD_SEARCH_BUDGET, so
# this reveals the network over roughly a dozen steps for a typical map.
STEP_COST_BUDGET = 60.0

# A bigger city fields a bigger road crew: its trips reveal faster
# (fewer steps to finish), independent of the traffic weight tier
# already earns it. Doesn't change *reach* - that's still gated by the
# real terrain-cost budget in _bounded_dijkstra - only how fast the
# ceiling-driven reveal advances for a trip once it's known to complete.
TIER_BUILD_SPEED_PER_LEVEL = 0.25


def _build_speed(tier: int) -> float:
    """Effective build speed for a source city's tier. Tier 1 is the
    baseline (1x); each tier above it adds another
    TIER_BUILD_SPEED_PER_LEVEL. A trip's reveal cost gets divided by
    this. A higher-tier source's roads then cross the same ceiling in
    fewer steps than an identical route from a smaller city."""
    return 1.0 + TIER_BUILD_SPEED_PER_LEVEL * (tier - 1)


class RoadsError(ValueError):
    """Something about roads' inputs doesn't make sense, reported to the
    UI as a sentence rather than a stack trace."""


_RoadTarget = namedtuple("_RoadTarget", "label x y weight")


def _resource_targets(
    package: mapfmt.Package, project: dict, mask: np.ndarray
) -> list[_RoadTarget]:
    """One target per painted resource blob, at its centroid. For a
    blob concave enough that the centroid lands outside it, uses the
    nearest actual pixel instead. Any class layer painting an
    ATTRACTION_KEYS color participates - same as growth.py's resource
    pull. A package doesn't have to call the layer "resources"."""
    targets: list[_RoadTarget] = []
    height, width = mask.shape
    for name, cfg in package.layers.items():
        if cfg.kind != "class":
            continue
        keys = [k for k in cfg.keys if k in growth.ATTRACTION_KEYS]
        if not keys:
            continue
        raster = export.rasterize_layer(project, package, cfg, package.size, None)
        for k in keys:
            rgb = np.array(mapfmt.hex_to_rgb(cfg.color_for_key(k)), dtype=np.uint8)
            blob_mask = np.all(raster == rgb, axis=-1) & mask
            if not blob_mask.any():
                continue
            n, labels, stats, centroids = cv2.connectedComponentsWithStats(
                blob_mask.astype(np.uint8), connectivity=8
            )
            weight = RESOURCE_TARGET_WEIGHT.get(k, 1.0)
            for label in range(1, n):
                if stats[label, cv2.CC_STAT_AREA] < MIN_RESOURCE_BLOB_PX:
                    continue
                cx, cy = centroids[label]
                cx, cy = round(cx), round(cy)
                if (
                    not (0 <= cx < width and 0 <= cy < height)
                    or labels[cy, cx] != label
                ):
                    ys, xs = np.nonzero(labels == label)
                    idx = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
                    cx, cy = int(xs[idx]), int(ys[idx])
                targets.append(_RoadTarget(f"{name}:{k}:{label}", cx, cy, weight))
    return targets


def _local_edges(
    local_mask: np.ndarray, local_cost: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every directed edge of the 8-connected grid graph inside a box,
    as (source_node, dest_node, weight). Uses the same shifted-array
    trick growth._shift uses. An ~8M-edge box then assembles in a
    handful of vectorized numpy ops, instead of a million Python-level
    dict/list insertions. Weight is the destination pixel's move cost,
    times the diagonal factor. That matches growth's rule that
    entering a pixel costs whatever the terrain there costs. Edges go
    one direction only, not symmetric - the same way growth's frontier
    expansion works."""
    box_h, box_w = local_mask.shape
    node_id = np.arange(box_h * box_w, dtype=np.int32).reshape(box_h, box_w)

    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for dy, dx, weight_factor in growth.neighbor_offsets:
        src_y0, src_y1 = max(0, -dy), box_h - max(0, dy)
        src_x0, src_x1 = max(0, -dx), box_w - max(0, dx)
        dst_y0, dst_y1 = max(0, dy), box_h - max(0, -dy)
        dst_x0, dst_x1 = max(0, dx), box_w - max(0, -dx)
        if src_y0 >= src_y1 or src_x0 >= src_x1:
            continue  # offset larger than the box itself

        src_valid = local_mask[src_y0:src_y1, src_x0:src_x1]
        dst_valid = local_mask[dst_y0:dst_y1, dst_x0:dst_x1]
        valid = src_valid & dst_valid
        if not valid.any():
            continue

        src_ids = node_id[src_y0:src_y1, src_x0:src_x1]
        dst_ids = node_id[dst_y0:dst_y1, dst_x0:dst_x1]
        edge_weight = local_cost[dst_y0:dst_y1, dst_x0:dst_x1] * weight_factor
        rows.append(src_ids[valid])
        cols.append(dst_ids[valid])
        weights.append(edge_weight[valid])

    if not rows:  # a source with no land neighbor at all (an isolated speck)
        empty = np.empty(0, dtype=np.int32)
        return empty, empty, empty.astype(np.float32)
    return np.concatenate(rows), np.concatenate(cols), np.concatenate(weights)


def _bounded_dijkstra(
    cost_grid: np.ndarray,
    mask: np.ndarray,
    cx: int,
    cy: int,
    budget: float,
    reach: int,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Single-source Dijkstra from (cx, cy), confined to a local box
    and cut off at `budget`. `reach` bounds the box: no path can
    travel more than budget/min_cost pixels from the source. So
    nothing outside the box could ever be reachable anyway.

    Runs via scipy's C-implemented sparse-graph Dijkstra rather than a
    hand-rolled Python heapq loop. A real-map box (budget ~500px, so
    a ~1000x1000 box) has close to a million nodes. A per-pixel
    Python loop over that many heap pops made a single city's search
    take long enough to look hung. _local_edges builds the sparse
    graph in a vectorized way too, so the whole search runs numpy/C
    end to end.

    Returns (dist, pred, y0, x0). dist/pred are arrays local to the
    box. pred[y, x] holds the flattened local index (y * box_w + x)
    of whichever pixel reached (y, x) most cheaply. At the source or
    an unreached pixel it's negative instead. Scipy's -9999 sentinel
    already satisfies _trace_path's "p < 0" stop condition, same as
    the -1 this used to use."""
    height, width = mask.shape
    y0, y1 = max(0, cy - reach), min(height, cy + reach + 1)
    x0, x1 = max(0, cx - reach), min(width, cx + reach + 1)
    local_mask = mask[y0:y1, x0:x1]
    local_cost = cost_grid[y0:y1, x0:x1]
    box_h, box_w = local_mask.shape
    n = box_h * box_w

    sy, sx = cy - y0, cx - x0
    source = sy * box_w + sx

    rows, cols, weights = _local_edges(local_mask, local_cost)
    graph = csr_matrix((weights, (rows, cols)), shape=(n, n))
    dist, pred = sparse_dijkstra(
        graph,
        directed=True,
        indices=source,
        limit=budget,
        return_predecessors=True,
    )
    return (
        dist.reshape(box_h, box_w).astype(np.float32),
        pred.reshape(box_h, box_w).astype(np.int64),
        y0,
        x0,
    )


def _trace_path(
    pred: np.ndarray, dist: np.ndarray, y0: int, x0: int, ty: int, tx: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Walk predecessors from local (ty, tx) back to the source, in
    global map coordinates. Returns every pixel a completed trip
    travels, plus each one's own cost-from-source. Sampled from the
    same `dist` grid the search already computed. That per-pixel cost
    is what lets start()/step() reveal a trip growing outward pixel by
    pixel as the ceiling rises. Otherwise it would only appear once
    its total cost clears the ceiling."""
    box_w = pred.shape[1]
    ys: list[int] = []
    xs: list[int] = []
    costs: list[float] = []
    y, x = ty, tx
    while True:
        ys.append(y + y0)
        xs.append(x + x0)
        costs.append(float(dist[y, x]))
        p = int(pred[y, x])
        if p < 0:
            break
        y, x = divmod(p, box_w)
    return (
        np.array(ys, dtype=np.intp),
        np.array(xs, dtype=np.intp),
        np.array(costs, dtype=np.float32),
    )


def _trips(weight: float, cost: float) -> float:
    """Traffic a completed round trip contributes. Scales with how
    much the two ends pull each other. Decays with the cost to get
    there. Caps so one dominant pair can't saturate a path alone."""
    return min(BASE_TRIP_SCALE * weight / (1.0 + cost / COST_NORM), MAX_TRIPS_PER_PAIR)


def _thin(mask: np.ndarray) -> np.ndarray:
    """One-pixel-wide centerline of a boolean blob, via Zhang-Suen
    thinning. cv2's thinning lives in the optional ximgproc contrib
    module (absent here) and skimage isn't a dependency. Hence this
    compact vectorized reimplementation. Each pass tests every
    foreground pixel's 8-neighborhood and peels boundary pixels that
    keep connectivity intact. The two Zhang-Suen subiterations alternate
    until no pixel remains to peel."""
    img = mask.astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for subiter in (0, 1):
            p = np.pad(img, 1)
            # P2..P9 clockwise from north, per the Zhang-Suen convention.
            neigh = [
                p[:-2, 1:-1],  # P2 N
                p[:-2, 2:],  # P3 NE
                p[1:-1, 2:],  # P4 E
                p[2:, 2:],  # P5 SE
                p[2:, 1:-1],  # P6 S
                p[2:, :-2],  # P7 SW
                p[1:-1, :-2],  # P8 W
                p[:-2, :-2],  # P9 NW
            ]
            b = sum(neigh)  # count of nonzero neighbors
            a = np.zeros_like(b)  # 0->1 transitions around the ring
            for i in range(8):
                a += ((neigh[i] == 0) & (neigh[(i + 1) % 8] == 1)).astype(np.uint8)
            p2, p4, p6, p8 = neigh[0], neigh[2], neigh[4], neigh[6]
            cond = (img == 1) & (b >= 2) & (b <= 6) & (a == 1)
            if subiter == 0:
                cond &= (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond &= (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            if cond.any():
                img[cond] = 0
                changed = True
    return img.astype(bool)


def _consolidate(traveled: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    """Merge near-parallel roads onto shared centerlines, summing their
    traffic. A morphological close with `radius` bridges the gap between
    any two roads within 2*radius of each other into one blob. Thinning
    that blob yields a single centerline down the merged corridor. Then
    every original weighted pixel hands its traffic to the nearest
    centerline pixel. Two parallel trails thus pool onto one line whose
    combined weight can climb a tier neither reaches alone. This conserves
    total traffic - weight only moves sideways onto the centerline, so the
    MAX_TRAVELED cap still bounds it."""
    if radius <= 0:
        return traveled
    road = traveled > 0
    if not road.any():
        return traveled
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    closed = (
        cv2.morphologyEx(road.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
        & mask
    )
    # Thin only the corridor's bounding box - Zhang-Suen sweeps the whole
    # array each pass, and the road network is a thin sliver of a big map.
    ys, xs = np.nonzero(closed)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    skel = _thin(closed[y0:y1, x0:x1])
    if not skel.any():
        return traveled

    # For each pixel in the box, the coordinates of its nearest centerline
    # pixel. road is a subset of closed, so every weighted pixel lies in
    # the box and lands on some centerline.
    _dist, (iy, ix) = ndi.distance_transform_edt(~skel, return_indices=True)
    out = np.zeros_like(traveled)
    rys, rxs = np.nonzero(road)
    ly, lx = rys - y0, rxs - x0
    np.add.at(out, (iy[ly, lx] + y0, ix[ly, lx] + x0), traveled[rys, rxs])
    return out


def _paint_raster(
    traveled: np.ndarray,
    road_cfg: mapfmt.LayerConfig,
    mask: np.ndarray,
    pending_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    traveled = _consolidate(traveled, mask, CONSOLIDATE_RADIUS_PX)
    height, width = traveled.shape
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = np.array(road_cfg.nodata_rgb, dtype=np.uint8)

    # Pending trails paint first so a completed tier - drawn after -
    # always wins where a still-growing trip's early pixels happen to
    # sit on ground another trip already finished.
    if (
        pending_mask is not None
        and pending_mask.any()
        and "road_pending" in road_cfg.keys
    ):
        blob = pending_mask
        if PENDING_WIDTH_PX > 1:
            kernel = np.ones((PENDING_WIDTH_PX, PENDING_WIDTH_PX), np.uint8)
            blob = cv2.dilate(blob.astype(np.uint8), kernel).astype(bool) & mask
        rgb = np.array(
            mapfmt.hex_to_rgb(road_cfg.color_for_key("road_pending")), dtype=np.uint8
        )
        canvas[blob] = rgb

    tier = np.zeros((height, width), dtype=np.uint8)
    tier[traveled > 0] = 1
    tier[traveled >= TIER2_MIN] = 2
    tier[traveled >= TIER3_MIN] = 3

    for level, key in ((1, "road_t1"), (2, "road_t2"), (3, "road_t3")):
        blob = tier == level
        if not blob.any():
            continue
        width_px = TIER_WIDTH_PX.get(level, 1)
        if width_px > 1:
            kernel = np.ones((width_px, width_px), np.uint8)
            blob = cv2.dilate(blob.astype(np.uint8), kernel).astype(bool) & mask
        rgb = np.array(mapfmt.hex_to_rgb(road_cfg.color_for_key(key)), dtype=np.uint8)
        canvas[blob] = rgb
    return canvas, tier


# Roads runs as a sequence of separate HTTP requests, same as growth.py.
# The trip list a session reveals from is expensive to recompute (a
# bounded Dijkstra per city) but invariant across a whole session, so a
# module-level cache keyed by package root lets step() skip rebuilding it
# on every request - mirrors growth._claim_cache.
_trip_cache: dict[str, dict] = {}


def _cache_key(package: mapfmt.Package) -> str:
    return f"{package.root}:{package.road_layer.name}"


_Trip = tuple[float, float, np.ndarray, np.ndarray, np.ndarray, int]


def _collect_trips(
    package: mapfmt.Package, project: dict
) -> tuple[list[_Trip], np.ndarray, list[tuple], int, int, int]:
    """Every completed trip, not yet painted: a source city's bounded
    search reaching a target within ROAD_SEARCH_BUDGET. Returned as
    (reveal_cost, trip_weight, path_ys, path_xs, reveal_costs,
    source_tier).

    reveal_cost/reveal_costs divide the physical terrain cost by the
    source city's _build_speed: a bigger city's crew reveals its
    roads faster. That lets start()/step() grow a trip outward pixel
    by pixel, at a pace depending on who's building it. Otherwise a
    trip would only appear once its total cost clears the ceiling.
    Reach and traffic weight (_trips()) both still use the raw
    physical cost, though. Build speed only changes how fast a
    known-reachable road gets drawn. It doesn't change how far a city
    can reach or how much traffic it earns.

    source_tier is the originating city's tier, along for the
    frontier marker. It shows which city's crew is building a given
    stretch. Also returns the land mask, the city list (id, x, y,
    tier), and resource/discarded counts for the caller's stats."""
    city_cfg = growth.city_layer(package)
    tier_field = growth.tier_field(city_cfg)
    city_points = mapfmt.project_points(project, city_cfg.name)
    if not city_points:
        raise RoadsError(f"no points authored on '{city_cfg.name}' yet")

    mask = growth.land_mask(package, project)
    cost_grid = growth.terrain_cost_grid(
        package, project, include_attraction=False, include_roads=False
    )
    height, width = mask.shape

    cities = []
    for city_id, payload in city_points.items():
        x, y = round(payload["x"]), round(payload["y"])
        if not (0 <= x < width and 0 <= y < height) or not mask[y, x]:
            raise RoadsError(
                f"city '{city_id}' at ({x}, {y}) isn't on "
                f"'{package.province_layer.clip_to}' - move it before building roads"
            )
        tier = max(1, int(payload.get(tier_field, 1)))
        cities.append((city_id, x, y, tier))
    cities.sort(key=lambda c: growth.sort_key(c[0]))

    resource_targets = _resource_targets(package, project, mask)

    land_costs = cost_grid[mask]
    min_cost = float(land_costs.min()) if land_costs.size else 1.0
    min_cost = max(min_cost, 1e-3)
    reach = int(ROAD_SEARCH_BUDGET / min_cost) + 2

    trips: list[_Trip] = []
    discarded_trips = 0

    for i, (_city_id, cx, cy, tier) in enumerate(cities):
        dist, pred, y0, x0 = _bounded_dijkstra(
            cost_grid, mask, cx, cy, ROAD_SEARCH_BUDGET, reach
        )
        box_h, box_w = dist.shape

        # A search from one city covers each unordered pair once - the
        # weight (tier(a) * tier(b)) doesn't care which end started it.
        candidates = [
            (ox, oy, tier * other_tier) for _id, ox, oy, other_tier in cities[i + 1 :]
        ]
        candidates += [
            (target.x, target.y, tier * target.weight) for target in resource_targets
        ]

        speed = _build_speed(tier)
        for tx, ty, weight in candidates:
            ly, lx = ty - y0, tx - x0
            if not (0 <= ly < box_h and 0 <= lx < box_w) or not np.isfinite(
                dist[ly, lx]
            ):
                discarded_trips += 1
                continue
            cost = float(dist[ly, lx])
            ys, xs, costs = _trace_path(pred, dist, y0, x0, ly, lx)
            trips.append(
                (cost / speed, _trips(weight, cost), ys, xs, costs / speed, tier)
            )

    return trips, mask, cities, len(resource_targets), discarded_trips, reach


def _write_raster(
    package: mapfmt.Package,
    road_cfg: mapfmt.LayerConfig,
    traveled: np.ndarray,
    mask: np.ndarray,
    pending_mask: np.ndarray | None = None,
) -> np.ndarray:
    canvas, tier_buf = _paint_raster(traveled, road_cfg, mask, pending_mask)
    path = package.raster_path(road_cfg.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).save(path)
    return tier_buf


# Cap on how many in-progress trips report a frontier marker - a session
# with hundreds of still-growing trips would otherwise hand the browser
# hundreds of orbs. Ranked by how close to finishing each one is, so the
# markers shown are always the most legible ones: about to land.
MAX_FRONTIER_MARKERS = 60


def _pending_state(
    trips: list[_Trip], next_index: int, ceiling: float, shape: tuple[int, int]
) -> tuple[np.ndarray, list[dict]]:
    """Everything still under construction at this ceiling.

    Returns a mask of every revealed-but-not-yet-committed pixel, for
    the "growing" preview trail. Also returns one frontier marker per
    such trip: the point on its path with the highest cost-from-source
    still <= ceiling. That's "where the agent is" - the
    farthest this trip's road has reached so far. Each marker carries
    its source city's tier too. A fast (high-tier) crew's progress
    then reads at a glance next to a slow one's."""
    pending_mask = np.zeros(shape, dtype=bool)
    frontier: list[tuple[float, dict]] = []
    for final_cost, _weight, ys, xs, costs, tier in trips[next_index:]:
        revealed = costs <= ceiling
        if not revealed.any():
            continue
        pending_mask[ys[revealed], xs[revealed]] = True
        revealed_costs = costs[revealed]
        tip = int(np.argmax(revealed_costs))
        remaining = final_cost - float(revealed_costs[tip])
        frontier.append(
            (
                remaining,
                {
                    "x": int(xs[revealed][tip]),
                    "y": int(ys[revealed][tip]),
                    "tier": tier,
                },
            )
        )
    frontier.sort(key=lambda f: f[0])
    return pending_mask, [
        marker for _remaining, marker in frontier[:MAX_FRONTIER_MARKERS]
    ]


def _meta_of(project: dict, road_cfg: mapfmt.LayerConfig) -> dict | None:
    return project.get("layers", {}).get(road_cfg.name, {}).get("build")


def _tier_stats(tier_buf: np.ndarray) -> dict:
    return {
        "tier1_px": int((tier_buf == 1).sum()),
        "tier2_px": int((tier_buf == 2).sum()),
        "tier3_px": int((tier_buf == 3).sum()),
    }


def start(package: mapfmt.Package, project: dict) -> dict:
    """(Re)seed a roads build session. Runs the whole trip search and
    wipes the layer to blank. Caches the sorted trip list, so step()
    has something to reveal. Mirrors growth.start() wiping the
    province layer. roads.py owns the layer from here on, until Start
    Over runs again or a hand edit lands on the raster."""
    road_cfg = package.road_layer
    if road_cfg is None:
        raise RoadsError("this package has no road_layer configured")

    trips, mask, cities, resources_n, discarded, reach = _collect_trips(
        package, project
    )
    trips.sort(key=lambda t: t[0])

    traveled = np.zeros(mask.shape, dtype=np.float32)
    pending_mask, frontier = _pending_state(trips, 0, 0.0, mask.shape)
    tier_buf = _write_raster(package, road_cfg, traveled, mask, pending_mask)

    # Only shown before the first step - once building starts, the real
    # revealed roads speak for themselves and the zones would just be
    # visual clutter over them. See step()'s meta.pop for the other half.
    search_zones = [
        {"x": cx, "y": cy, "radius": reach} for _city_id, cx, cy, _tier in cities
    ]

    meta = {
        "step": 0,
        "ceiling": 0.0,
        "total_trips": len(trips),
        "painted_trips": 0,
        "discarded_trips": discarded,
        "cities": len(cities),
        "resource_targets": resources_n,
        "search_zones": search_zones,
    }
    project.setdefault("layers", {}).setdefault(road_cfg.name, {})["build"] = meta

    _trip_cache[_cache_key(package)] = {
        "trips": trips,
        "mask": mask,
        "traveled": traveled,
        "next_index": 0,
        "ceiling": 0.0,
    }

    return {**meta, "done": not trips, "frontier": frontier, **_tier_stats(tier_buf)}


def step(package: mapfmt.Package, project: dict) -> dict:
    """Raise the cost ceiling by STEP_COST_BUDGET and paint every trip
    that now falls under it, cheapest first. The session ends the step
    that reveals the last trip. Each trip gets one round of traffic, with
    no extra maturation rounds. Busy shared corridors still climb tiers.
    Every trip stacks its own weight on the shared pixels, and that
    stacking is all that ever drove trunk roads."""
    road_cfg = package.road_layer
    if road_cfg is None:
        raise RoadsError("this package has no road_layer configured")

    meta = _meta_of(project, road_cfg)
    if not meta:
        raise RoadsError("no roads build in progress - press Start Over first")

    # The search-zone preview is only for the pre-step state (see
    # start()) - the first step onward, drop it so it stops overlaying
    # the roads actually being drawn.
    meta.pop("search_zones", None)

    cache_key = _cache_key(package)
    cached = _trip_cache.get(cache_key)
    if cached is None or len(cached["trips"]) != meta["total_trips"]:
        # Cache miss (fresh server process, or a hand edit landed on the
        # roads layer between steps) - recompute the trip list and
        # fast-forward it to the ceiling already recorded in the
        # project, so a step after a reload resumes where it left off.
        trips, mask, _cities, _resources_n, _discarded, _reach = _collect_trips(
            package, project
        )
        trips.sort(key=lambda t: t[0])
        traveled = np.zeros(mask.shape, dtype=np.float32)
        ceiling = float(meta.get("ceiling", 0.0))
        next_index = 0
        for cost, weight, ys, xs, _costs, _tier in trips:
            if cost > ceiling:
                break
            traveled[ys, xs] += weight
            next_index += 1
        cached = {
            "trips": trips,
            "mask": mask,
            "traveled": traveled,
            "next_index": next_index,
            "ceiling": ceiling,
        }

    trips = cached["trips"]
    mask = cached["mask"]
    traveled = cached["traveled"]
    next_index = cached["next_index"]
    ceiling = cached["ceiling"] + STEP_COST_BUDGET

    painted_this_step = 0
    while next_index < len(trips) and trips[next_index][0] <= ceiling:
        _cost, weight, ys, xs, _costs, _tier = trips[next_index]
        traveled[ys, xs] += weight
        next_index += 1
        painted_this_step += 1

    traveled = np.minimum(traveled, MAX_TRAVELED)

    meta["step"] += 1
    meta["ceiling"] = ceiling
    meta["painted_trips"] = next_index
    done = next_index >= len(trips)

    pending_mask, frontier = _pending_state(trips, next_index, ceiling, mask.shape)
    tier_buf = _write_raster(package, road_cfg, traveled, mask, pending_mask)

    _trip_cache[cache_key] = {
        "trips": trips,
        "mask": mask,
        "traveled": traveled,
        "next_index": next_index,
        "ceiling": ceiling,
    }

    print(
        f"roads step {meta['step']}: {painted_this_step} trip(s) revealed "
        f"({next_index}/{len(trips)} total, {meta['discarded_trips']} out of reach)"
    )

    return {
        **meta,
        "painted_this_step": painted_this_step,
        "done": done,
        "frontier": frontier,
        **_tier_stats(tier_buf),
    }
