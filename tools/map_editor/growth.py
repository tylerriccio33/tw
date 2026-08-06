"""Grows the province layer outward from authored city points.

A province starts as a dot at its city and expands one discrete step
at a time. A higher-tier city's border moves further per step. A
contested pixel goes to whichever city grows faster. Growth never
crosses the coastline or eats a pixel another city already claimed.

Nothing here names a layer "provinces" or "cities". It reads whatever
map.json's province_layer and city_layer point to, and finds the tier
by field *type*, not by name.

Each step commits immediately. It rasterizes the province layer's
current polygons and grows the claim buffer. Then it revectorizes
the result back into the province layer's own `features`. That's the
same round trip Fill Gaps and Clean Shapes use. A grown province
looks like a hand-traced one downstream.
"""

from __future__ import annotations

import json
import re

import cv2
import export
import mapfmt
import numpy as np

PIXELS_PER_TIER_STEP = 18  # how far a tier-1 city's border moves in one step
SEED_RADIUS_PX = 4

# A front that reaches a sticky pixel (a legend key marked "sticky": true,
# e.g. a mountain range) pauses there instead of climbing straight over it.
# It keeps growing anywhere else that step, and gets STICKY_HOLD_STEPS
# attempts (building "temperature") before it's allowed to push across.
# This is a heuristic, not a physical simulation - see growth.py's module
# docstring and the "gravity" request this implements.
STICKY_HOLD_STEPS = 4

# trim_to_sticky_boundaries' cut zone: how far from a sticky pixel a claim
# can be before it's considered "just barely" past the boundary and worth
# reassigning to whichever city is actually closer.
CUT_MARGIN_PX = 6


class GrowthError(ValueError):
    """Something about growth's inputs doesn't make sense, reported to the
    UI as a sentence rather than a stack trace."""


# Growth runs as a sequence of separate HTTP requests, each reloading the
# project from disk - the process outlives any one request, though, so a
# module-level cache keyed by package root lets consecutive steps skip
# work that's invariant across the whole growth session: the land mask
# never changes, and the claim buffer from the previous step is exactly
# the input the next step needs (no reason to rebuild it from polygons).
_land_mask_cache: dict[str, tuple[str, np.ndarray]] = {}
_sticky_mask_cache: dict[str, tuple[str, np.ndarray]] = {}
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


STICKY_KEYS = frozenset({"mountains", "forest", "river"})


def _sticky_layers(package: mapfmt.Package) -> list[tuple[str, list[str]]]:
    """Layers with legend keys in `STICKY_KEYS` (mountains, forest, river -
    natural border growth gravitates toward and sticks at these).
    Returns (layer name, sticky keys) pairs."""
    out = []
    for name, cfg in package.layers.items():
        if cfg.kind != "class":
            continue
        sticky_keys = [
            entry["key"] for entry in cfg.legend.values() if entry["key"] in STICKY_KEYS
        ]
        if sticky_keys:
            out.append((name, sticky_keys))
    return out


def _sticky_fingerprint(
    package: mapfmt.Package, sticky_layers: list[tuple[str, list[str]]]
) -> str:
    parts = []
    for name, _keys in sticky_layers:
        cfg = package.layers[name]
        if cfg.input == "brush":
            path = package.raster_path(name)
            try:
                stat = path.stat()
                parts.append((name, stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                parts.append((name, None, None))
        else:
            # A brush usually paints sticky terrain; this just avoids
            # crashing on a polygon/point class layer.
            parts.append((name, "non-brush"))
    return repr(parts)


def _sticky_mask(package: mapfmt.Package, project: dict) -> np.ndarray:
    """Boolean union of every sticky-flagged pixel across every class
    layer. All-False (and effectively free once cached) on a package with
    no sticky terrain authored - growth behaves exactly as before."""
    height, width = package.size[1], package.size[0]
    sticky_layers = _sticky_layers(package)
    if not sticky_layers:
        return np.zeros((height, width), dtype=bool)

    key = _cache_key(package)
    fingerprint = _sticky_fingerprint(package, sticky_layers)
    cached = _sticky_mask_cache.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    mask = np.zeros((height, width), dtype=bool)
    for name, keys in sticky_layers:
        cfg = package.layers[name]
        raster = export.rasterize_layer(project, package, cfg, package.size, None)
        for k in keys:
            rgb = np.array(mapfmt.hex_to_rgb(cfg.color_for_key(k)), dtype=np.uint8)
            mask |= np.all(raster == rgb, axis=-1)
    _sticky_mask_cache[key] = (fingerprint, mask)
    return mask


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

    bbox: dict[str, list[int]] = {}
    claim = np.zeros((height, width), dtype=np.int32)
    for province_id, city_id in seed_of.items():
        payload = city_points[city_id]
        x, y = float(payload["x"]), float(payload["y"])
        cv2.circle(claim, (int(x), int(y)), SEED_RADIUS_PX, int(province_id), -1)
        bbox[city_id] = [
            max(0, int(y) - SEED_RADIUS_PX),
            min(height, int(y) + SEED_RADIUS_PX + 1),
            max(0, int(x) - SEED_RADIUS_PX),
            min(width, int(x) + SEED_RADIUS_PX + 1),
        ]

    province_layer = package.province_layer.name
    project.setdefault("layers", {}).setdefault(province_layer, {})
    project["layers"][province_layer]["features"] = features
    meta = {
        "step": 0,
        "seed_of": seed_of,
        "tier_field": tier_field,
        "finished_cities": [],
        "bbox": bbox,
        "sticky_hold": {},
    }
    project["layers"][province_layer]["growth"] = meta

    _claim_cache[_cache_key(package)] = {
        "step": 0,
        "seed_of": dict(seed_of),
        "claim": claim,
    }

    return {"features": features, "growth": meta, "province_count": len(features)}


def step(package: mapfmt.Package, project: dict) -> dict:
    """Grow every still-growing province outward by one step, resolving
    contested pixels toward whichever city has the higher tier."""
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
    sticky = _sticky_mask(package, project)
    sticky_hold: dict[str, int] = meta.setdefault("sticky_hold", {})
    size = (mask.shape[1], mask.shape[0])
    height, width = mask.shape
    prior_step = int(meta.get("step", 0))

    cache_key = _cache_key(package)
    cached = _claim_cache.get(cache_key)
    if (
        cached is not None
        and cached["step"] == prior_step
        and cached["seed_of"] == seed_of
        and cached["claim"].shape == mask.shape
    ):
        claim = cached["claim"]
    else:
        # Cache miss (fresh server process, or a hand edit landed on the
        # province layer between steps) - fall back to rebuilding the
        # claim buffer from the polygons on record, same as before.
        raster = export.rasterize_polygon_layer(project, province_cfg, size)
        claim = export.id_buffer(raster)
    new_claim = claim.copy()
    taken_this_step = np.zeros_like(claim, dtype=bool)

    kernel_cache: dict[int, np.ndarray] = {}

    def kernel(radius: int) -> np.ndarray:
        if radius not in kernel_cache:
            kernel_cache[radius] = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
            )
        return kernel_cache[radius]

    def tier_of(city_id: str) -> int:
        payload = city_points.get(city_id) or {}
        return int(payload.get(tier_field, 1))

    # Higher tier first: a pixel contested by two cities in the same step
    # goes to whichever grows faster, the same way a bigger city's pull
    # on the land around it would win.
    order = sorted(seed_of.items(), key=lambda kv: -tier_of(kv[1]))

    # A province that hit the coastline or a neighbor on all sides never
    # grows again - once claim/mask decide it's boxed in, there's no
    # reason to keep dilating and bbox-scanning it on every future step.
    # Growth meta persists this set so it survives across the
    # separate HTTP requests each step arrives as.
    already_finished: set[str] = set(meta.get("finished_cities", []))
    bbox_of: dict[str, list[int]] = meta.get("bbox", {})

    growing_cities: list[str] = []
    finished_cities: list[str] = []
    for pid_str, city_id in order:
        if city_id in already_finished:
            continue
        pid = int(pid_str)
        if city_id not in city_points:
            finished_cities.append(city_id)  # city deleted; its province just stops
            continue
        radius = max(1, tier_of(city_id)) * PIXELS_PER_TIER_STEP

        # Grow (and bbox-scan) only the padded box around this city's
        # last known true extent - tracked incrementally step to step -
        # instead of an ever-widening estimate from the seed point. A
        # province boxed in by neighbors stays cheap to check instead of
        # costing more every step regardless of whether it can still grow.
        by0, by1, bx0, bx1 = bbox_of.get(city_id, [0, height, 0, width])
        y0 = max(0, by0 - radius)
        y1 = min(height, by1 + radius)
        x0 = max(0, bx0 - radius)
        x1 = min(width, bx1 + radius)

        own_full = claim[y0:y1, x0:x1] == pid
        if not own_full.any():
            finished_cities.append(city_id)
            continue
        own = own_full.astype(np.uint8)
        grown = cv2.dilate(own, kernel(radius)) > 0
        available = (
            grown
            & mask[y0:y1, x0:x1]
            & (claim[y0:y1, x0:x1] == 0)
            & ~taken_this_step[y0:y1, x0:x1]
        )

        # Gravity: a front that reaches a sticky boundary (river, mountain
        # range) sets aside those pixels and keeps growing everywhere else,
        # building "temperature" each step it's still touching one. Only
        # once that reaches STICKY_HOLD_STEPS does it punch through and
        # claim the sticky pixels too, same as everything else.
        sticky_available = available & sticky[y0:y1, x0:x1]
        if sticky_available.any():
            hold = sticky_hold.get(city_id, 0) + 1
            if hold < STICKY_HOLD_STEPS:
                sticky_hold[city_id] = hold
                available = available & ~sticky[y0:y1, x0:x1]
            else:
                sticky_hold[city_id] = 0  # broke through - reset for the next one
        else:
            sticky_hold.pop(city_id, None)

        if not available.any():
            if sticky_available.any():
                # Paused at the boundary, not boxed in - try again next step.
                growing_cities.append(city_id)
                continue
            finished_cities.append(city_id)
            continue
        new_claim[y0:y1, x0:x1][available] = pid
        taken_this_step[y0:y1, x0:x1] |= available
        growing_cities.append(city_id)

        # Tighten the tracked bbox to the true extent (prior claim plus
        # what just got added), all within the already-small slice.
        ys, xs = np.nonzero(own_full | available)
        bbox_of[city_id] = [
            y0 + int(ys.min()),
            y0 + int(ys.max()) + 1,
            x0 + int(xs.min()),
            x0 + int(xs.max()) + 1,
        ]

    already_finished.update(finished_cities)
    meta["finished_cities"] = sorted(already_finished)
    meta["bbox"] = bbox_of

    changed_px = int((new_claim != claim).sum())
    features = export.revectorize(export.id_raster(new_claim), province_cfg, project)
    project["layers"][province_cfg.name]["features"] = features
    meta["step"] = int(meta.get("step", 0)) + 1
    print(
        f"growth step {meta['step']}: {changed_px}px changed, "
        f"{len(growing_cities)} growing, {len(finished_cities)} finished this step "
        f"({len(already_finished)}/{len(seed_of)} total finished)"
    )
    _claim_cache[cache_key] = {
        "step": meta["step"],
        "seed_of": dict(seed_of),
        "claim": new_claim,
    }

    return {
        "features": features,
        "growth": meta,
        "step": meta["step"],
        "changed_px": changed_px,
        "growing_cities": growing_cities,
        "finished_cities": finished_cities,
        "done": changed_px == 0,
    }


def trim_to_sticky_boundaries(
    package: mapfmt.Package, project: dict, margin_px: int = CUT_MARGIN_PX
) -> dict:
    """Post-processing pass for after growth finishes.

    Pulls each province's claim back onto a sticky boundary (river,
    mountain range) instead of stopping a few pixels short.

    Growth's tier-order tie-breaking can let one province nose slightly
    across a natural border first. That leaves an arbitrary bulge on the
    "wrong" side. This doesn't route the border exactly along the
    boundary. Within margin_px of any sticky pixel, it reassigns the
    pixel to whichever city center is nearest. That's a Voronoi re-cut
    restricted to the boundary strip. A placeholder heuristic, not a
    promise the cut always lands well.

    Idempotent: rerunning with nothing changed reports changed_px == 0.
    """
    province_cfg = package.province_layer
    meta = project.get("layers", {}).get(province_cfg.name, {}).get("growth")
    if not meta or not meta.get("seed_of"):
        raise GrowthError("no grown provinces to trim - run Start/Step first")

    city_cfg = _city_layer(package)
    city_points = mapfmt.project_points(project, city_cfg.name)
    seed_of: dict[str, str] = meta["seed_of"]

    sticky = _sticky_mask(package, project)
    features = project["layers"][province_cfg.name]["features"]
    if not sticky.any():
        return {"features": features, "changed_px": 0}

    raster = export.rasterize_polygon_layer(project, province_cfg, package.size)
    claim = export.id_buffer(raster)

    radius = max(1, int(margin_px))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
    )
    zone = cv2.dilate(sticky.astype(np.uint8), kernel) > 0
    zone &= claim > 0

    ys, xs = np.nonzero(zone)
    if ys.size == 0:
        return {"features": features, "changed_px": 0}

    city_ids = sorted(seed_of, key=lambda pid: int(pid))
    centers = np.array(
        [
            [city_points[seed_of[pid]]["x"], city_points[seed_of[pid]]["y"]]
            for pid in city_ids
        ]
    )
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    dist2 = ((points[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    nearest = np.array([int(city_ids[i]) for i in dist2.argmin(axis=1)])

    new_claim = claim.copy()
    new_claim[ys, xs] = nearest

    changed_px = int((new_claim != claim).sum())
    features = export.revectorize(export.id_raster(new_claim), province_cfg, project)
    project["layers"][province_cfg.name]["features"] = features

    cache_key = _cache_key(package)
    cached = _claim_cache.get(cache_key)
    if cached is not None:
        cached["claim"] = new_claim

    print(f"growth trim: {changed_px}px reassigned near sticky boundaries")
    return {"features": features, "changed_px": changed_px}
