#!/usr/bin/env python3
"""Render a map package as one reviewable PNG, without a browser.

Composites the layer stack in manifest order over the real backdrop. A
border that misses the shore is then obvious at a glance. It also runs
the same validate_package the Export button runs. Offending polygons
turn up in red on the image, not just as a name in text.

The province layer uses each province's editor swatch, not its id color.
provinces.png is machine-readable by design and shows up as near-black.
This is the human-readable view of it.

Usage:
    uv run preview.py
    uv run preview.py --package-dir dev_map_data --out /tmp/preview.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import export
import mapfmt
import numpy as np
from PIL import Image, ImageDraw

DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"

# Per-layer opacity over the backdrop. Anything not listed composites at
# DEFAULT_ALPHA, so a newly added layer shows up instead of being invisible.
LAYER_ALPHA = {
    "coastline": 90,
    "provinces": 130,
    "terrain": 110,
    "resources": 200,
    "ownership": 150,
}
DEFAULT_ALPHA = 120


def _swatches(
    project: dict, package: mapfmt.Package
) -> dict[int, tuple[int, int, int]]:
    swatches = {}
    for feature in mapfmt.project_features(project, package.province_layer.name):
        if "id" in feature:
            swatches[int(feature["id"])] = mapfmt.hex_to_rgb(
                feature.get("color", "#808080")
            )
    return swatches


def _layer_overlay(
    raster: np.ndarray,
    cfg: mapfmt.LayerConfig,
    alpha: int,
    swatches: dict[int, tuple[int, int, int]],
) -> Image.Image:
    height, width = raster.shape[:2]
    rgba = np.zeros((height, width, 4), dtype=np.uint8)

    if cfg.kind == "identity":
        ids = export.id_buffer(raster)
        for province_id, rgb in swatches.items():
            mask = ids == province_id
            rgba[mask, :3] = rgb
            rgba[mask, 3] = alpha
    else:
        nodata = np.array(cfg.nodata_rgb, dtype=np.uint8)
        painted = ~np.all(raster == nodata, axis=-1)
        rgba[painted, :3] = raster[painted]
        rgba[painted, 3] = alpha

    return Image.fromarray(rgba, mode="RGBA")


def render_preview(
    project: dict, package: mapfmt.Package
) -> tuple[Image.Image, list[str]]:
    with Image.open(package.backdrop_path) as src:
        composite = src.convert("RGBA")

    problems = export.validate_package(project, package)
    swatches = _swatches(project, package)

    for name in package.layer_order:
        path = package.raster_path(name)
        if not path.is_file():
            continue  # not exported yet
        with Image.open(path) as im:
            raster = np.array(im.convert("RGB"))
        overlay = _layer_overlay(
            raster, package.layers[name], LAYER_ALPHA.get(name, DEFAULT_ALPHA), swatches
        )
        composite = Image.alpha_composite(composite, overlay)

    composite = composite.convert("RGB")

    flagged = export.problem_polygons(project, package)
    if flagged:
        draw = ImageDraw.Draw(composite)
        for pts in flagged:
            draw.polygon(pts, outline=(255, 0, 0), width=3)
            for x, y in pts:
                draw.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 0, 0), width=2)

    return composite, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(DEV_DIR_DEFAULT))
    parser.add_argument("--out", default=None, help="Defaults to <package>/preview.png")
    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    package = mapfmt.load_package(package_dir)
    project = mapfmt.load_project(package_dir, package)

    composite, problems = render_preview(project, package)

    out_path = Path(args.out) if args.out else package_dir / "preview.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path)

    print(f"wrote {out_path}")
    if problems:
        print(f"\n{len(problems)} problem(s) found (outlined in red on the preview):")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("no validation problems")


if __name__ == "__main__":
    main()
