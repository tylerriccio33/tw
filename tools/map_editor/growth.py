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

import re

import cv2
import export
import mapfmt
import numpy as np

PIXELS_PER_TIER_STEP = 6  # how far a tier-1 city's border moves in one step
SEED_RADIUS_PX = 4


class GrowthError(ValueError):
    """Something about growth's inputs doesn't make sense, reported to the
    UI as a sentence rather than a stack trace."""


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
    cfg = package.province_layer
    if not cfg.clip_to:
        raise GrowthError(
            f"province layer '{cfg.name}' has no clip_to mask to grow within"
        )
    return export.mask_bool(package, project, cfg.clip_to)


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

    province_layer = package.province_layer.name
    project.setdefault("layers", {}).setdefault(province_layer, {})
    project["layers"][province_layer]["features"] = features
    meta = {"step": 0, "seed_of": seed_of, "tier_field": tier_field}
    project["layers"][province_layer]["growth"] = meta

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
    size = (mask.shape[1], mask.shape[0])
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

    growing_cities: list[str] = []
    finished_cities: list[str] = []
    for pid_str, city_id in order:
        pid = int(pid_str)
        if city_id not in city_points:
            finished_cities.append(city_id)  # city deleted; its province just stops
            continue
        own = (claim == pid).astype(np.uint8)
        if not own.any():
            finished_cities.append(city_id)
            continue
        radius = max(1, tier_of(city_id)) * PIXELS_PER_TIER_STEP
        grown = cv2.dilate(own, kernel(radius)) > 0
        available = grown & mask & (claim == 0) & ~taken_this_step
        if not available.any():
            finished_cities.append(city_id)
            continue
        new_claim[available] = pid
        taken_this_step |= available
        growing_cities.append(city_id)

    changed_px = int((new_claim != claim).sum())
    features = export.revectorize(export.id_raster(new_claim), province_cfg, project)
    project["layers"][province_cfg.name]["features"] = features
    meta["step"] = int(meta.get("step", 0)) + 1

    return {
        "features": features,
        "growth": meta,
        "step": meta["step"],
        "changed_px": changed_px,
        "growing_cities": growing_cities,
        "finished_cities": finished_cities,
        "done": changed_px == 0,
    }
