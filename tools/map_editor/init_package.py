#!/usr/bin/env python3
"""Create a fresh map package from a backdrop image.

Writes the manifest, the layer configs, and the faction roster. It also
traces a coastline from the backdrop's line art and seeds a terrain layer
to plains. Provinces start empty - they're what you draw in the editor.

The coastline autotrace is the *only* place the land/sea brightness
heuristic in coastline.py still runs. It exists to give you a starting
outline rather than making you click around the whole continent by hand.
From that point on the coastline counts as authored data. You edit it,
other layers clip and snap to it, and nothing re-guesses it from pixels.

Usage:
    uv run init_package.py                        # into dev_map_data/
    uv run init_package.py --seed-provinces 12    # + a placeholder partition
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import coastline
import cv2
import mapfmt
import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"
GAME_DIR_DEFAULT = REPO_ROOT / "campaign" / "map_data"

SEA_COLOR = "#1d3550"
LAND_COLOR = "#f2efe6"

FACTIONS = [
    {"key": "red", "name": "Red", "color": "#cd5c5c", "money": 100},
    {"key": "blue", "name": "Blue", "color": "#6495ed", "money": 100},
    {"key": "green", "name": "Green", "color": "#3cb371", "money": 100},
    {"key": "yellow", "name": "Yellow", "color": "#daa520", "money": 100},
]

TERRAIN_LEGEND = {
    "#7d9a4e": {"key": "plains", "name": "Plains", "move_cost": 1.0},
    "#8a7a5a": {"key": "hills", "name": "Hills", "move_cost": 1.5},
    "#6b6b6b": {"key": "mountains", "name": "Mountains", "move_cost": 2.5},
    "#2f6d3d": {"key": "forest", "name": "Forest", "move_cost": 1.8},
    "#c9b688": {"key": "desert", "name": "Desert", "move_cost": 1.4},
}

RESOURCE_LEGEND = {
    "#9aa0a6": {"key": "iron", "name": "Iron"},
    "#d4af37": {"key": "gold", "name": "Gold"},
    "#7b3f00": {"key": "timber", "name": "Timber"},
    "#8e2f4a": {"key": "wine", "name": "Wine"},
    "#e8e8e8": {"key": "salt", "name": "Salt"},
}

# Palette the editor cycles through as provinces appear. These are display
# swatches for the editor and preview only - a province's color on the
# game map is whoever owns it this turn.
PROVINCE_PALETTE = [
    "#4363d8",
    "#f58231",
    "#911eb4",
    "#46f0f0",
    "#bfef45",
    "#fabed4",
    "#469990",
    "#dcbeff",
    "#9a6324",
    "#808000",
    "#ffd8b1",
    "#000075",
    "#c65102",
    "#c50202",
    "#c5ab02",
    "#3cb44b",
    "#e6194b",
    "#42d4f4",
]

COAST_MIN_CONTOUR_AREA = 200  # px^2, drops speckle islands
COAST_SIMPLIFY_EPSILON = 1.5  # px


def manifest(size: tuple[int, int]) -> dict:
    return {
        "format_version": mapfmt.FORMAT_VERSION,
        "size": [size[0], size[1]],
        "backdrop": "backdrop.png",
        "factions": "factions.json",
        "province_layer": "provinces",
        # Order is the draw order, the export order, and the default snap
        # order all at once. Trace the shore, cut provinces against it,
        # paint terrain inside those, then assign who starts where.
        "layers": ["coastline", "provinces", "terrain", "resources", "ownership"],
    }


def layer_configs() -> dict[str, dict]:
    return {
        "coastline": {
            "name": "coastline",
            "title": "Coastline",
            "input": "polygon",
            "kind": "mask",
            "raster": "coastline.png",
            # Unpainted pixels read as sea, so absence alone marks the
            # ocean - you trace the land, never the water.
            "nodata_color": SEA_COLOR,
            "default_key": "land",
            "snap_source": True,
            "legend": {
                LAND_COLOR: {"key": "land", "name": "Land"},
                SEA_COLOR: {"key": "sea", "name": "Sea"},
            },
        },
        "provinces": {
            "name": "provinces",
            "title": "Provinces",
            "input": "polygon",
            "kind": "identity",
            "raster": "provinces.png",
            "id_encoding": "rgb24",
            "nodata_color": "#000000",
            "snap_source": True,
            "clip_to": "coastline:land",
            "gapfill": {"max_gap_px": 12, "within": "coastline:land"},
        },
        "terrain": {
            "name": "terrain",
            "title": "Terrain",
            "input": "brush",
            "kind": "class",
            "raster": "terrain.png",
            "nodata_color": "#000000",
            "default_key": "plains",
            "snap_source": False,
            "clip_to": "coastline:land",
            "gapfill": {"max_gap_px": 12, "within": "coastline:land"},
            "legend": TERRAIN_LEGEND,
            "reduce": {"into": "terrain", "mode": "majority"},
        },
        "resources": {
            "name": "resources",
            "title": "Resources",
            "input": "brush",
            "kind": "class",
            "raster": "resources.png",
            "nodata_color": "#000000",
            "default_key": "iron",
            "snap_source": False,
            "clip_to": "coastline:land",
            "legend": RESOURCE_LEGEND,
            "reduce": {"into": "resources", "mode": "any"},
        },
        "ownership": {
            "name": "ownership",
            "title": "Starting owners",
            "input": "assign",
            "kind": "class",
            "raster": "ownership.png",
            "nodata_color": "#000000",
            "snap_source": False,
            "legend": {
                f["color"]: {"key": f["key"], "name": f["name"]} for f in FACTIONS
            },
            "reduce": {"into": "starting_owner", "mode": "majority"},
        },
    }


def trace_land_features(land_mask: np.ndarray) -> list[dict]:
    """Outer contours become `land`. Holes inside them are inland seas,
    and come back as `sea`, listed after so they punch back through."""
    contours, hierarchy = cv2.findContours(
        land_mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return []

    land: list[list[list[float]]] = []
    sea: list[list[list[float]]] = []
    for i, contour in enumerate(contours):
        if cv2.contourArea(contour) < COAST_MIN_CONTOUR_AREA:
            continue
        simplified = cv2.approxPolyDP(contour, COAST_SIMPLIFY_EPSILON, True)
        pts = [[round(float(p[0][0]), 1), round(float(p[0][1]), 1)] for p in simplified]
        if len(pts) < 3:
            continue
        (sea if int(hierarchy[0][i][3]) != -1 else land).append(pts)

    features = []
    if land:
        features.append({"key": "land", "polygons": land})
    if sea:
        features.append({"key": "sea", "polygons": sea})
    return features


def seed_provinces(land_mask: np.ndarray, count: int) -> list[dict]:
    """Chop the landmass into `count` placeholder provinces.

    Purely a bootstrap, so the game has something to run on before anyone
    traces a real border. Farthest-point sampling spreads seeds over the
    land, then every land pixel joins its nearest seed. The result is
    contiguous, entirely on land, and fully connected, which is all the
    pipeline needs. The borders mean nothing; retrace them.
    """
    ys, xs = np.nonzero(land_mask)
    if len(ys) == 0 or count < 1:
        return []

    # Farthest-point sampling: start at the land centroid's nearest pixel,
    # then repeatedly take the land pixel furthest from every seed so far.
    # Spreads seeds over the whole continent instead of clumping them the
    # way random picks do.
    first = int(np.argmin((xs - xs.mean()) ** 2 + (ys - ys.mean()) ** 2))
    seeds = [(int(xs[first]), int(ys[first]))]
    best = (xs - seeds[0][0]) ** 2 + (ys - seeds[0][1]) ** 2
    for _ in range(count - 1):
        pick = int(np.argmax(best))
        seeds.append((int(xs[pick]), int(ys[pick])))
        best = np.minimum(best, (xs - seeds[-1][0]) ** 2 + (ys - seeds[-1][1]) ** 2)

    # Nearest-seed assignment via one distance transform: zeros at the
    # seeds, labels come back as "which seed is nearest".
    src = np.full(land_mask.shape, 255, dtype=np.uint8)
    for sx, sy in seeds:
        src[sy, sx] = 0
    _dist, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, cv2.DIST_MASK_PRECISE, labelType=cv2.DIST_LABEL_PIXEL
    )
    label_of_seed = {int(labels[sy, sx]): i for i, (sx, sy) in enumerate(seeds)}

    features = []
    for label, index in sorted(label_of_seed.items(), key=lambda kv: kv[1]):
        mask = ((labels == label) & land_mask).astype(np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polygons = []
        for contour in contours:
            if cv2.contourArea(contour) < COAST_MIN_CONTOUR_AREA:
                continue
            simplified = cv2.approxPolyDP(contour, COAST_SIMPLIFY_EPSILON, True)
            pts = [
                [round(float(p[0][0]), 1), round(float(p[0][1]), 1)] for p in simplified
            ]
            if len(pts) >= 3:
                polygons.append(pts)
        if not polygons:
            continue
        name = f"Province {index + 1}"
        features.append(
            {
                "id": index + 1,
                "key": mapfmt.slugify(name),
                "name": name,
                "color": PROVINCE_PALETTE[index % len(PROVINCE_PALETTE)],
                "polygons": polygons,
            }
        )
    return features


def seed_terrain_raster(land_mask: np.ndarray, path: Path) -> None:
    """Land starts as plains. Everything else gets painted by hand -
    inventing terrain would be inventing gameplay."""
    height, width = land_mask.shape
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    plains = mapfmt.hex_to_rgb(
        next(k for k, v in TERRAIN_LEGEND.items() if v["key"] == "plains")
    )
    canvas[land_mask] = np.array(plains, dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).save(path)


def seed_empty_raster(size: tuple[int, int], path: Path) -> None:
    width, height = size
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8)).save(path)


def init_package(
    package_dir: Path, backdrop: Path, province_count: int = 0, force: bool = False
) -> dict:
    package_dir = Path(package_dir)
    manifest_path = package_dir / mapfmt.MANIFEST_NAME
    if manifest_path.is_file() and not force:
        raise SystemExit(
            f"{manifest_path} already exists - pass --force to overwrite the "
            "configs (rasters and project.json are left alone)"
        )

    with Image.open(backdrop) as im:
        size = im.size

    coastline.assert_clean_source(backdrop)
    land_mask = coastline.build_land_mask(backdrop)

    layers_dir = package_dir / mapfmt.LAYERS_DIRNAME
    layers_dir.mkdir(parents=True, exist_ok=True)

    manifest_path.write_text(json.dumps(manifest(size), indent=1) + "\n")
    (package_dir / "factions.json").write_text(json.dumps(FACTIONS, indent=1) + "\n")
    for name, cfg in layer_configs().items():
        (layers_dir / f"{name}.json").write_text(json.dumps(cfg, indent=1) + "\n")

    if not (package_dir / "backdrop.png").is_file():
        shutil.copy2(backdrop, package_dir / "backdrop.png")

    seed_terrain_raster(land_mask, layers_dir / "terrain.png")
    seed_empty_raster(size, layers_dir / "resources.png")

    provinces = seed_provinces(land_mask, province_count)
    project = {
        "format_version": mapfmt.PROJECT_FORMAT_VERSION,
        "size": [size[0], size[1]],
        "active_layer": "provinces",
        "layers": {
            "coastline": {"features": trace_land_features(land_mask)},
            "provinces": {"features": provinces},
            "terrain": {},
            "resources": {},
            "ownership": {
                "assignments": {
                    str(p["id"]): FACTIONS[i % len(FACTIONS)]["key"]
                    for i, p in enumerate(provinces)
                }
            },
        },
    }
    (package_dir / mapfmt.PROJECT_NAME).write_text(json.dumps(project, indent=1) + "\n")

    return {
        "size": list(size),
        "land_px": int(land_mask.sum()),
        "coastline_features": len(project["layers"]["coastline"]["features"]),
        "provinces": len(provinces),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(DEV_DIR_DEFAULT))
    parser.add_argument("--backdrop", default=str(GAME_DIR_DEFAULT / "backdrop.png"))
    parser.add_argument(
        "--seed-provinces",
        type=int,
        default=0,
        help="Chop the land into N placeholder provinces so the game runs "
        "before anything is traced by hand.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    summary = init_package(
        Path(args.package_dir),
        Path(args.backdrop),
        args.seed_provinces,
        args.force,
    )
    print(f"package at {args.package_dir}")
    print(f"  size:               {summary['size'][0]}x{summary['size'][1]}")
    print(f"  land:               {summary['land_px']} px")
    print(f"  coastline features: {summary['coastline_features']}")
    print(f"  provinces:          {summary['provinces']}")
    print("\nNext: make map-editor  (then Export)")


if __name__ == "__main__":
    main()
