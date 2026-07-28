#!/usr/bin/env python3
"""Render the current dev_map_data/project.json as a single reviewable
PNG, without going through the browser.

Runs the same validate_project + export_project path the editor's
Export button uses. What you see here is what a real export produces.
Region colors overlay the terrain backdrop, not a flat white canvas,
so coastline misalignment is visible at a glance. Bad polygons get
outlined in red on the image, not just named in text.

Usage:
    uv run preview.py
    uv run preview.py --project dev_map_data/project.json --out /tmp/preview.png
"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

import server as srv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"
GAME_DIR_DEFAULT = REPO_ROOT / "campaign" / "map_data"

REGION_ALPHA = 170  # 0-255, how opaque region fills are over the backdrop


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.strip().lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def _problem_polygons(
    project: dict, size: tuple[int, int]
) -> list[list[tuple[float, float]]]:
    """Re-run validate_project's per-polygon checks, but return the
    offending point lists instead of message strings, so we can draw them."""
    width, height = size
    flagged = []
    for region in project.get("regions", []):
        name = region.get("name", "").strip()
        if not name:
            continue
        for polygon in region.get("polygons", []):
            if len(polygon) < 3:
                continue
            pts = [(float(x), float(y)) for x, y in polygon]
            out_of_bounds = any(
                not (0 <= x <= width and 0 <= y <= height) for x, y in pts
            )
            duplicate_point = len(set(pts)) < len(pts)
            crosses = duplicate_point or srv.polygon_self_intersects(pts)
            if out_of_bounds or crosses:
                flagged.append(pts)
    return flagged


def render_preview(project: dict, image_path: Path) -> tuple[Image.Image, list[str]]:
    with Image.open(image_path) as src:
        backdrop = src.convert("RGB")
    size = backdrop.size

    problems = srv.validate_project(project, size)

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for region in project.get("regions", []):
        name = region.get("name", "").strip()
        if not name:
            continue
        rgb = _hex_to_rgb(region.get("color", "#808080"))
        for polygon in region.get("polygons", []):
            if len(polygon) < 3:
                continue
            pts = [(float(x), float(y)) for x, y in polygon]
            draw.polygon(pts, fill=(*rgb, REGION_ALPHA))

    composite = Image.alpha_composite(backdrop.convert("RGBA"), overlay).convert("RGB")

    if problems:
        outline_draw = ImageDraw.Draw(composite)
        for pts in _problem_polygons(project, size):
            outline_draw.polygon(pts, outline=(255, 0, 0), width=3)
            for x, y in pts:
                r = 4
                outline_draw.ellipse(
                    [x - r, y - r, x + r, y + r], outline=(255, 0, 0), width=2
                )

    return composite, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=str(DEV_DIR_DEFAULT / "project.json"),
        help="Project file to render.",
    )
    parser.add_argument(
        "--image",
        default=str(GAME_DIR_DEFAULT / "backdrop.png"),
        help="Terrain backdrop to overlay regions on.",
    )
    parser.add_argument(
        "--out",
        default=str(DEV_DIR_DEFAULT / "preview.png"),
        help="Where to write the rendered preview PNG.",
    )
    args = parser.parse_args()

    project = json.loads(Path(args.project).read_text())
    composite, problems = render_preview(project, Path(args.image))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path)

    print(f"wrote {out_path}")
    if problems:
        print(f"\n{len(problems)} problem(s) found (outlined in red on the preview):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    print("no validation problems")


if __name__ == "__main__":
    main()
