"""Grows the province layer outward from authored city points.

A province starts as a dot at its city. It expands as a shared,
multi-source weighted frontier - Dijkstra's algorithm, in effect.
Every step advances that frontier by a fixed cost budget. Entering a
pixel costs whatever `move_cost` the terrain (or any other class layer)
assigns it. Growth bends toward resource tiles like a tendril reaching
for gold or iron. A bigger city's tier divides that cost, so its
frontier eats the same terrain more cheaply. Growth never crosses the
coastline or eats a pixel another city already claimed. Whichever
city's frontier reaches a pixel at the lowest accumulated cost wins
it. Contested ground resolves by cost, not by iteration order.

Nothing here names a layer "provinces", "cities", "terrain" or
"resources". It reads whatever map.json's province_layer and city_layer
point to, and finds the tier by field *type*. Movement cost and
resource pull, though, are deliberately *not* per-map data.
TERRAIN_MOVE_COST, ATTRACTION_KEYS and ROAD_MOVE_COST below form a
single global table, shared by every map package, keyed by legend key
name. Any class layer painting a "mountains" pixel costs the same to
cross on every map. Balance tuning lives here in one place instead of
drifting between map.json packages.

A built road layer (roads.py's output - see its road_layer keys
road_t1/t2/t3) is just another cost-grid entry. Wherever
a road already runs, growth's frontier crosses at the road's cost
instead of the terrain's. So a province growing along an established
trade route reaches further per step than one crossing open ground.
The Dijkstra machinery itself never changes - the accelerant is
entirely in the per-pixel cost, same as terrain and resources.

Each step commits immediately. It advances the shared frontier by one
cost band, then revectorizes the result back into the province layer's
own `features`. That's the same round trip Fill Gaps and Clean Shapes
use. A grown province looks like a hand-traced one downstream.
"""

from __future__ import annotations

import heapq
import itertools
import json
import re

import cv2
import export
import mapfmt
import numpy as np

# Cost units the shared frontier advances per step. On plain terrain
# (move_cost 1.0) a tier-1 city's border moves this many pixels per
# step, the same pacing the prior fixed-radius model used, so a bare
# map with no terrain painted grows at a familiar speed.
STEP_COST_BUDGET = 18.0

SEED_RADIUS_PX = 4

# Global movement-cost table, keyed by legend key name rather than
# authored per map: painting "mountains" on any package always costs
# this much to cross. A key not listed here (including every non-terrain
# key - faction colors, tier bands, ...) costs the default 1.0. Any class
# layer whose legend uses these key names participates automatically, so
# a second cost-bearing layer (roads, say) just needs to reuse a key
# name from this table, or get another entry added here.
TERRAIN_MOVE_COST: dict[str, float] = {
    "plains": 1.0,
    "hills": 1.5,
    "mountains": 20,
    "forest": 3,
    "desert": 5,
}

# Legend keys that pull growth's frontier toward them, same
# global-table deal as move cost above. Proximity to one of these
# discounts a pixel's move cost, bottoming out at
# (1 - ATTRACTION_STRENGTH) of normal cost right at the tile and
# fading out linearly past this radius.
ATTRACTION_KEYS: frozenset[str] = frozenset(
    {
        "iron",
        "timber",
        "wine",
        "salt",
        "wool",
        "cloth",
        "tin",
        "coal",
        "lead",
        "silver",
        "fish",
    }
)
ATTRACTION_RADIUS_PX = 60.0
ATTRACTION_STRENGTH = 0.6

# A road is an improvement over whatever terrain it crosses, not a
# discount on top of it - a tier-3 road through mountains costs this much
# to enter, full stop, not 20 (mountains) times some fraction. So this
# table caps a pixel's cost rather than multiplying it, same global-table
# convention as TERRAIN_MOVE_COST/ATTRACTION_KEYS: any class layer
# painting these key names participates automatically, keyed by roads.py's
# own tier keys since a road's pull is exactly "how built-up is this
# stretch". Cost is never allowed to collapse near zero - that would let
# a single step's cost budget jump the frontier arbitrarily far across
# the map - so tier 3 bottoms out just above MIN_COST_FACTOR.
ROAD_MOVE_COST: dict[str, float] = {
    "road_t1": 0.6,
    "road_t2": 0.35,
    "road_t3": 0.2,
}

# Cost is never allowed to collapse near zero - that would let a single
# step's cost budget jump the frontier arbitrarily far across the map.
MIN_COST_FACTOR = 0.15

# sqrt(2) weight for diagonal steps, so an 8-connected frontier
# approximates Euclidean distance instead of blowing up into a square.
_DIAGONAL = 2**0.5
_NEIGHBOR_OFFSETS = (
    (-1, -1, _DIAGONAL),
    (-1, 0, 1.0),
    (-1, 1, _DIAGONAL),
    (0, -1, 1.0),
    (0, 1, 1.0),
    (1, -1, _DIAGONAL),
    (1, 0, 1.0),
    (1, 1, _DIAGONAL),
)


class GrowthError(ValueError):
    """Something about growth's inputs doesn't make sense, reported to the
    UI as a sentence rather than a stack trace."""


# Growth runs as a sequence of separate HTTP requests, each reloading the
# project from disk - the process outlives any one request, though, so a
# module-level cache keyed by package root lets consecutive steps skip
# work that's invariant across the whole growth session: the land mask
# and terrain cost never change mid-session, and the claim/frontier state
# from the previous step is exactly the input the next step needs (no
# reason to rebuild it from polygons).
_land_mask_cache: dict[str, tuple[str, np.ndarray]] = {}
_terrain_cost_cache: dict[str, tuple[str, np.ndarray]] = {}
_claim_cache: dict[str, dict] = {}


def _cache_key(package: mapfmt.Package) -> str:
    return f"{package.root}:{package.province_layer.name}"


def _mask_fingerprint(package: mapfmt.Package, project: dict) -> str:
    layer_name = package.province_layer.clip_to.split(":", 1)[0]
    return json.dumps(project.get("layers", {}).get(layer_name, {}), sort_keys=True)


def _tier_field(cfg: mapfmt.LayerConfig) -> str:
    for field_name, field_cfg in cfg.point_fields.items():
        if field_cfg.get("type") == "tier":
            return field_name
    raise GrowthError(f"city layer '{cfg.name}' has no point_fields entry of type=tier")


def _city_layer(package: mapfmt.Package) -> mapfmt.LayerConfig:
    cfg = package.city_layer
    if cfg is None:
        raise GrowthError("this package has no city_layer configured")
    if cfg.point_coupling != "free":
        raise GrowthError(
            f"city layer '{cfg.name}' must be point_coupling=free to grow "
            "provinces from it"
        )
    return cfg


def _land_mask(package: mapfmt.Package, project: dict) -> np.ndarray:
    """Cached per package for the life of the server process. The
    coastline a province clips to stays fixed while growth runs.
    Re-rasterizing it at full map resolution on every step was pure
    waste. Invalidated automatically if the clip_to layer's data
    changes underneath us (e.g. a hand edit between steps)."""
    cfg = package.province_layer
    if not cfg.clip_to:
        raise GrowthError(
            f"province layer '{cfg.name}' has no clip_to mask to grow within"
        )
    key = _cache_key(package)
    fingerprint = _mask_fingerprint(package, project)
    cached = _land_mask_cache.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    mask = export.mask_bool(package, project, cfg.clip_to)
    _land_mask_cache[key] = (fingerprint, mask)
    return mask


def _layers_fingerprint(package: mapfmt.Package, layer_names: list[str]) -> str:
    parts = []
    for name in layer_names:
        cfg = package.layers[name]
        if cfg.input == "brush":
            path = package.raster_path(name)
            try:
                stat = path.stat()
                parts.append((name, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                parts.append((name, None, None))
        else:
            # A brush usually paints terrain/resources; this just avoids
            # crashing on a polygon/point class layer.
            parts.append((name, "non-brush"))
    return repr(parts)


def _move_cost_layers(package: mapfmt.Package) -> list[str]:
    """Class layers that paint at least one key listed in
    TERRAIN_MOVE_COST. Generic over layer name: any layer reusing e.g.
    "mountains" as a key picks up the global cost automatically.
    The *values* come from that fixed table, not this package's
    legend."""
    return [
        name
        for name, cfg in package.layers.items()
        if cfg.kind == "class" and any(k in TERRAIN_MOVE_COST for k in cfg.keys)
    ]


def _attraction_layers(package: mapfmt.Package) -> list[tuple[str, list[str]]]:
    """Class layers that paint at least one key listed in
    ATTRACTION_KEYS - growth's frontier bends toward those pixels."""
    out = []
    for name, cfg in package.layers.items():
        if cfg.kind != "class":
            continue
        keys = [k for k in cfg.keys if k in ATTRACTION_KEYS]
        if keys:
            out.append((name, keys))
    return out


def _road_cost_layer(package: mapfmt.Package) -> tuple[str, list[str]] | None:
    """The road layer, if this package has one and it paints at least
    one key listed in ROAD_MOVE_COST. Growth's frontier then races
    down already-built roads, the same way it bends toward resources."""
    cfg = package.road_layer
    if cfg is None:
        return None
    keys = [k for k in cfg.keys if k in ROAD_MOVE_COST]
    return (cfg.name, keys) if keys else None


def _terrain_cost_grid(
    package: mapfmt.Package,
    project: dict,
    *,
    include_attraction: bool = True,
    include_roads: bool = True,
) -> np.ndarray:
    """Per-pixel float32 multiplier on move cost, from the global
    TERRAIN_MOVE_COST/ATTRACTION_KEYS/ROAD_MOVE_COST tables rather than
    anything authored in this package. Move-cost layers multiply in
    (default 1.0 where nothing's painted, or the layer's default_key's
    cost). Attraction layers then discount the result toward
    resources. A road layer caps the result low wherever a road
    already runs. A package that paints none of those key names gets
    all-ones, free everywhere but the coastline. Growth then behaves
    like a plain unweighted frontier.

    `include_attraction=False` skips the resource-discount pass.
    roads.py wants pure terrain cost, since its targets already *are*
    the resources. Bending the path toward unrelated ore on the way
    would double-dip the same pull. `include_roads=False` likewise
    keeps roads.py's own search from racing down roads it's still
    building. It wants the raw terrain cost of a route that doesn't
    exist yet, not a route discounted by itself."""
    height, width = package.size[1], package.size[0]
    move_layers = _move_cost_layers(package)
    attract_layers = _attraction_layers(package) if include_attraction else []
    road_layer = _road_cost_layer(package) if include_roads else None
    relevant = move_layers + [name for name, _ in attract_layers]
    if road_layer is not None:
        relevant = relevant + [road_layer[0]]
    if not relevant:
        return np.ones((height, width), dtype=np.float32)

    key = (
        _cache_key(package)
        + ("" if include_attraction else ":no_attract")
        + ("" if include_roads else ":no_roads")
    )
    fingerprint = _layers_fingerprint(package, relevant)
    cached = _terrain_cost_cache.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    cost = np.ones((height, width), dtype=np.float32)
    for name in move_layers:
        cfg = package.layers[name]
        raster = export.rasterize_layer(project, package, cfg, package.size, None)
        buf, keys = export.key_buffer(raster, cfg)
        lookup = np.ones(len(keys) + 1, dtype=np.float32)
        if cfg.default_key:
            lookup[0] = TERRAIN_MOVE_COST.get(cfg.default_key, 1.0)  # unpainted pixels
        for i, k in enumerate(keys):
            lookup[i + 1] = TERRAIN_MOVE_COST.get(k, 1.0)
        cost *= lookup[buf]

    if attract_layers:
        attraction = np.zeros((height, width), dtype=bool)
        for name, keys in attract_layers:
            cfg = package.layers[name]
            raster = export.rasterize_layer(project, package, cfg, package.size, None)
            for k in keys:
                rgb = np.array(mapfmt.hex_to_rgb(cfg.color_for_key(k)), dtype=np.uint8)
                attraction |= np.all(raster == rgb, axis=-1)
        if attraction.any():
            far = (~attraction).astype(np.uint8) * 255
            dist = cv2.distanceTransform(far, cv2.DIST_L2, 5)
            closeness = np.clip(1.0 - dist / ATTRACTION_RADIUS_PX, 0.0, 1.0)
            cost *= (1.0 - ATTRACTION_STRENGTH * closeness).astype(np.float32)

    if road_layer is not None:
        road_name, road_keys = road_layer
        cfg = package.layers[road_name]
        raster = export.rasterize_layer(project, package, cfg, package.size, None)
        for k in road_keys:
            rgb = np.array(mapfmt.hex_to_rgb(cfg.color_for_key(k)), dtype=np.uint8)
            blob = np.all(raster == rgb, axis=-1)
            if blob.any():
                cost = np.where(blob, np.minimum(cost, ROAD_MOVE_COST[k]), cost)

    cost = np.maximum(cost, MIN_COST_FACTOR).astype(np.float32)
    _terrain_cost_cache[key] = (fingerprint, cost)
    return cost


def _sort_key(point_id: str) -> tuple[int, str]:
    # City ids are "p1", "p2", ... - sort by the trailing number so
    # province id assignment is stable and doesn't put "p10" before "p2".
    m = re.search(r"(\d+)$", point_id)
    return (int(m.group(1)) if m else 0, point_id)


def _seed_polygon(
    x: float, y: float, radius: float = SEED_RADIUS_PX
) -> list[list[float]]:
    return [
        [x + radius * float(np.cos(a)), y + radius * float(np.sin(a))]
        for a in np.linspace(0, 2 * np.pi, 8, endpoint=False)
    ]


def _shift(arr: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    """out[y, x] == arr[y + dy, x + dx], `fill` past the map edge."""
    out = np.full_like(arr, fill)
    height, width = arr.shape
    src_y0, src_y1 = max(0, dy), height + min(0, dy)
    src_x0, src_x1 = max(0, dx), width + min(0, dx)
    dst_y0, dst_y1 = max(0, -dy), height + min(0, -dy)
    dst_x0, dst_x1 = max(0, -dx), width + min(0, -dx)
    out[dst_y0:dst_y1, dst_x0:dst_x1] = arr[src_y0:src_y1, src_x0:src_x1]
    return out


def _seed_heap(
    claim: np.ndarray,
    dist: np.ndarray,
    cost_grid: np.ndarray,
    mask: np.ndarray,
    tier_of,
) -> tuple[list, dict[int, int], itertools.count]:
    """Build the initial Dijkstra frontier from whatever's already
    claimed. Every unclaimed land pixel touching a claim gets one heap
    entry per claimed neighbor. Both start() and step()'s cold-cache
    fallback call this. A growth session can then always resume from
    just the claim raster on record."""
    heap: list[tuple[float, int, int, int, int]] = []
    frontier_count: dict[int, int] = {}
    seq = itertools.count()
    for dy, dx, weight_factor in _NEIGHBOR_OFFSETS:
        neighbor_claim = _shift(claim, dy, dx, 0)
        neighbor_dist = _shift(dist, dy, dx, np.inf)
        candidate = (claim == 0) & mask & (neighbor_claim != 0)
        if not candidate.any():
            continue
        ys, xs = np.nonzero(candidate)
        pids = neighbor_claim[ys, xs]
        base = neighbor_dist[ys, xs]
        entering = cost_grid[ys, xs] * weight_factor
        for y, x, pid, base_cost, enter_cost in zip(
            ys.tolist(), xs.tolist(), pids.tolist(), base.tolist(), entering.tolist()
        ):
            pid = int(pid)
            total = base_cost + enter_cost / tier_of(pid)
            heapq.heappush(heap, (total, next(seq), pid, y, x))
            frontier_count[pid] = frontier_count.get(pid, 0) + 1
    return heap, frontier_count, seq


def start(package: mapfmt.Package, project: dict) -> dict:
    """(Re)seed the province layer from the current city points, one tiny
    province per city, step 0. Wipes whatever the province layer
    currently holds - growth owns it from here until a hand edit."""
    city_cfg = _city_layer(package)
    tier_field = _tier_field(city_cfg)
    city_points = mapfmt.project_points(project, city_cfg.name)
    if not city_points:
        raise GrowthError(f"no points authored on '{city_cfg.name}' yet")

    mask = _land_mask(package, project)
    height, width = mask.shape

    seed_of: dict[str, str] = {}
    features: list[dict] = []
    for province_id, city_id in enumerate(sorted(city_points, key=_sort_key), start=1):
        payload = city_points[city_id]
        x, y = float(payload["x"]), float(payload["y"])
        if not (0 <= x <= width and 0 <= y <= height) or not mask[int(y), int(x)]:
            raise GrowthError(
                f"city '{city_id}' at ({x:.0f}, {y:.0f}) isn't on "
                f"'{package.province_layer.clip_to}' - move it before growing"
            )
        seed_of[str(province_id)] = city_id
        features.append(
            {
                "id": province_id,
                "key": mapfmt.slugify(f"province {province_id}"),
                "name": f"Province {province_id}",
                "polygons": [_seed_polygon(x, y)],
            }
        )

    claim = np.zeros((height, width), dtype=np.int32)
    for province_id, city_id in seed_of.items():
        payload = city_points[city_id]
        x, y = float(payload["x"]), float(payload["y"])
        cv2.circle(claim, (int(x), int(y)), SEED_RADIUS_PX, int(province_id), -1)

    province_layer = package.province_layer.name
    project.setdefault("layers", {}).setdefault(province_layer, {})
    project["layers"][province_layer]["features"] = features
    meta = {
        "step": 0,
        "seed_of": seed_of,
        "tier_field": tier_field,
        "finished_cities": [],
        "ceiling": 0.0,
    }
    project["layers"][province_layer]["growth"] = meta

    def tier_of(pid: int) -> int:
        payload = city_points.get(seed_of.get(str(pid))) or {}
        return max(1, int(payload.get(tier_field, 1)))

    cost_grid = _terrain_cost_grid(package, project)
    dist = np.where(claim > 0, 0.0, np.inf).astype(np.float32)
    heap, frontier_count, seq = _seed_heap(claim, dist, cost_grid, mask, tier_of)

    _claim_cache[_cache_key(package)] = {
        "step": 0,
        "seed_of": dict(seed_of),
        "claim": claim,
        "dist": dist,
        "heap": heap,
        "frontier_count": frontier_count,
        "seq": seq,
        "ceiling": 0.0,
    }

    return {"features": features, "growth": meta, "province_count": len(features)}


def step(package: mapfmt.Package, project: dict) -> dict:
    """Advance the shared weighted frontier by one cost budget. Claim
    every pixel whose cheapest path from any city now costs no more
    than the raised ceiling. Contested pixels resolve to whichever
    city's path reached them at the lower cost, not by tier order."""
    province_cfg = package.province_layer
    meta = (
        project.setdefault("layers", {})
        .setdefault(province_cfg.name, {})
        .setdefault("growth", {})
    )
    seed_of = meta.get("seed_of")
    if not seed_of:
        raise GrowthError("no growth in progress - press Start Over first")

    city_cfg = _city_layer(package)
    tier_field = meta.get("tier_field") or _tier_field(city_cfg)
    city_points = mapfmt.project_points(project, city_cfg.name)

    mask = _land_mask(package, project)
    cost_grid = _terrain_cost_grid(package, project)
    height, width = mask.shape
    prior_step = int(meta.get("step", 0))

    def tier_of(pid: int) -> int:
        payload = city_points.get(seed_of.get(str(pid))) or {}
        return max(1, int(payload.get(tier_field, 1)))

    cache_key = _cache_key(package)
    cached = _claim_cache.get(cache_key)
    if (
        cached is not None
        and cached["step"] == prior_step
        and cached["seed_of"] == seed_of
        and cached["claim"].shape == mask.shape
    ):
        claim = cached["claim"]
        dist = cached["dist"]
        heap = cached["heap"]
        frontier_count = cached["frontier_count"]
        seq = cached["seq"]
        ceiling = cached["ceiling"]
    else:
        # Cache miss (fresh server process, or a hand edit landed on the
        # province layer between steps) - rebuild the claim buffer from
        # the polygons on record and reseed the frontier from its
        # boundary, same fallback growth has always had.
        raster = export.rasterize_polygon_layer(project, province_cfg, (width, height))
        claim = export.id_buffer(raster)
        dist = np.where(claim > 0, 0.0, np.inf).astype(np.float32)
        heap, frontier_count, seq = _seed_heap(claim, dist, cost_grid, mask, tier_of)
        ceiling = float(meta.get("ceiling", 0.0))

    already_finished: set[str] = set(meta.get("finished_cities", []))
    ceiling += STEP_COST_BUDGET

    growing_pids: set[int] = set()
    claimed_px = 0
    while heap and heap[0][0] <= ceiling:
        cost, _, pid, y, x = heapq.heappop(heap)
        frontier_count[pid] -= 1
        if claim[y, x] != 0:
            continue  # stale - someone already claimed this pixel
        city_id = seed_of.get(str(pid))
        if city_id is None or city_id not in city_points:
            continue  # city deleted; its province just stops

        claim[y, x] = pid
        dist[y, x] = cost
        claimed_px += 1
        growing_pids.add(pid)

        tier = tier_of(pid)
        for dy, dx, weight_factor in _NEIGHBOR_OFFSETS:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < height and 0 <= nx < width):
                continue
            if claim[ny, nx] != 0 or not mask[ny, nx]:
                continue
            entry_cost = cost + cost_grid[ny, nx] * weight_factor / tier
            heapq.heappush(heap, (entry_cost, next(seq), pid, ny, nx))
            frontier_count[pid] = frontier_count.get(pid, 0) + 1

    finished_cities = []
    for pid_str, city_id in seed_of.items():
        if city_id in already_finished:
            continue
        if city_id not in city_points:
            finished_cities.append(city_id)
            continue
        if frontier_count.get(int(pid_str), 0) <= 0:
            finished_cities.append(city_id)

    already_finished.update(finished_cities)
    meta["finished_cities"] = sorted(already_finished)
    meta["ceiling"] = ceiling

    features = export.revectorize(export.id_raster(claim), province_cfg, project)
    project["layers"][province_cfg.name]["features"] = features
    meta["step"] = prior_step + 1

    growing_cities = sorted(
        seed_of[str(pid)] for pid in growing_pids if str(pid) in seed_of
    )
    print(
        f"growth step {meta['step']}: {claimed_px}px changed, "
        f"{len(growing_cities)} growing, {len(finished_cities)} finished this step "
        f"({len(already_finished)}/{len(seed_of)} total finished)"
    )

    _claim_cache[cache_key] = {
        "step": meta["step"],
        "seed_of": dict(seed_of),
        "claim": claim,
        "dist": dist,
        "heap": heap,
        "frontier_count": frontier_count,
        "seq": seq,
        "ceiling": ceiling,
    }

    return {
        "features": features,
        "growth": meta,
        "step": meta["step"],
        "changed_px": claimed_px,
        "growing_cities": growing_cities,
        "finished_cities": finished_cities,
        "done": not heap,
    }


# roads.py builds its own single-source searches over the same land mask,
# terrain cost table, and city/tier reading province growth uses - these
# names are the reuse surface, kept together so the contract is visible
# from either module instead of roads.py reaching for underscored names.
city_layer = _city_layer
tier_field = _tier_field
land_mask = _land_mask
terrain_cost_grid = _terrain_cost_grid
neighbor_offsets = _NEIGHBOR_OFFSETS
sort_key = _sort_key
