#!/usr/bin/env python3
"""Local map editor server.

Serves a browser-based tool for tracing faction/territory borders on top
of a terrain image. Everything the editor writes goes to a *dev* location:
tools/map_editor/dev_map_data/. That's never the live game data, so you
can iterate freely. Run `make promote-map` from the repo root once a
trace is ready. That copies it into campaign/map_data/, the directory
campaign/province_map.gd actually loads at runtime.

The dev and game directories use the same two-file format
province_map.gd expects. region_map.png is a flat-colored bitmap, one
solid color per territory. regions.txt maps each hex color to a
territory name, e.g. "#hexcolor": "Territory_Name". See
campaign/province_map.gd for how those become polygons.

On startup the editor loads, in priority order:
  1. dev_map_data/project.json   - a previous editing session (full point
     precision, the real source of truth once you've started editing).
  2. dev_map_data/region_map.png + regions.txt - a previously exported dev
     map, re-traced from its raster via contour detection.
  3. campaign/map_data/region_map.png + regions.txt - the map currently
     shipped in the game, same re-trace fallback. This is how you bootstrap
     an editing session from what's live today.
  4. an empty project, if none of the above exist.

Usage:
    cd tools/map_editor
    uv run server.py

Then open http://localhost:8765 and start tracing.
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from gapfill import fill_land_gaps

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"
GAME_DIR_DEFAULT = REPO_ROOT / "campaign" / "map_data"

MIN_CONTOUR_AREA = 12  # px^2, drops single-pixel noise contours on import
SIMPLIFY_EPSILON = 1.2  # px, cv2.approxPolyDP tolerance on import

COASTLINE_WORK_WIDTH = 900  # px, classification runs on a downscaled copy for speed
COASTLINE_BLUE_BIAS = 8  # min (B - R) for a pixel to count as "sea"-toned
COASTLINE_MORPH_KERNEL = 7  # px, smooths the sea mask and drops speckle before tracing
COASTLINE_MIN_CONTOUR_AREA = 120  # px^2 at working resolution, drops speckle contours
COASTLINE_SIMPLIFY_EPSILON = 2.0  # px at working resolution


def make_handler(args: argparse.Namespace):
    image_path = Path(args.image).resolve()
    dev_dir = Path(args.dev_dir).resolve()
    game_dir = Path(args.game_dir).resolve()
    project_path = dev_dir / "project.json"
    coastline_cache_path = dev_dir / "coastline.json"
    coastline_cache: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass  # keep stdout quiet

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str = None):
            if not path.is_file():
                self.send_error(404, "Not found")
                return
            data = path.read_bytes()
            ctype = (
                content_type
                or mimetypes.guess_type(str(path))[0]
                or "application/octet-stream"
            )
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/" or path == "/index.html":
                self._send_file(STATIC_DIR / "index.html", "text/html")
            elif path.startswith("/static/"):
                rel = path[len("/static/") :]
                self._send_file(STATIC_DIR / rel)
            elif path == "/api/image":
                self._send_file(image_path)
            elif path == "/api/project":
                self._send_json(
                    load_project(project_path, dev_dir, game_dir, image_path)
                )
            elif path == "/api/coastline":
                nonlocal coastline_cache
                if not coastline_cache:
                    coastline_cache = load_or_build_coastline(
                        image_path, coastline_cache_path
                    )
                self._send_json(coastline_cache)
            elif path == "/api/reload-from-game":
                project = import_from_raster(game_dir, image_path)
                if project is None:
                    self._send_json(
                        {"error": "no map in campaign/map_data to import"}, 404
                    )
                else:
                    self._send_json(project)
            else:
                self.send_error(404, "Not found")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "bad json"}, 400)
                return

            if self.path == "/api/project":
                dev_dir.mkdir(parents=True, exist_ok=True)
                project_path.write_text(json.dumps(payload, indent=2))
                self._send_json({"ok": True})
            elif self.path == "/api/export":
                try:
                    result = export_project(payload, image_path, dev_dir)
                    self._send_json({"ok": True, **result})
                except Exception as exc:  # surface errors to the UI
                    self._send_json({"ok": False, "error": str(exc)}, 500)
            else:
                self.send_error(404, "Not found")

    return Handler


def load_project(
    project_path: Path, dev_dir: Path, game_dir: Path, image_path: Path
) -> dict:
    if project_path.is_file():
        return json.loads(project_path.read_text())

    project = import_from_raster(dev_dir, image_path)
    if project is not None:
        return project

    project = import_from_raster(game_dir, image_path)
    if project is not None:
        return project

    with Image.open(image_path) as im:
        size = im.size
    return {"image_size": list(size), "regions": []}


def import_from_raster(source_dir: Path, image_path: Path) -> dict | None:
    """Re-trace an existing region_map.png/regions.txt pair into editable
    polygons via contour detection, rescaled into image_path's pixel space."""
    region_map_path = source_dir / "region_map.png"
    regions_txt_path = source_dir / "regions.txt"
    if not region_map_path.is_file() or not regions_txt_path.is_file():
        return None

    names_by_color = json.loads(regions_txt_path.read_text())

    with Image.open(region_map_path) as im:
        raster = np.array(im.convert("RGB"))
    raster_h, raster_w = raster.shape[:2]

    with Image.open(image_path) as target_im:
        target_w, target_h = target_im.size
    scale_x = target_w / raster_w
    scale_y = target_h / raster_h

    regions = []
    for hex_color, raw_name in names_by_color.items():
        rgb = tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
        mask = (
            np.all(raster == np.array(rgb, dtype=np.uint8), axis=-1).astype(np.uint8)
            * 255
        )
        if not mask.any():
            continue

        contours, hierarchy = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        polygons = []
        for contour in contours:
            if cv2.contourArea(contour) < MIN_CONTOUR_AREA:
                continue
            simplified = cv2.approxPolyDP(contour, SIMPLIFY_EPSILON, True)
            pts = [
                [
                    round(float(pt[0][0]) * scale_x, 1),
                    round(float(pt[0][1]) * scale_y, 1),
                ]
                for pt in simplified
            ]
            if len(pts) >= 3:
                polygons.append(pts)

        if polygons:
            regions.append(
                {
                    "name": raw_name.replace("_", " "),
                    "color": hex_color,
                    "polygons": polygons,
                }
            )

    return {"image_size": [target_w, target_h], "regions": regions}


def load_or_build_coastline(image_path: Path, cache_path: Path) -> dict:
    """Return {"lines": [[[x,y],...], ...]} tracing the land/sea boundary
    of image_path, in that image's full-resolution pixel space. Cached to
    disk keyed on the source image's mtime so repeat loads are instant."""
    mtime = image_path.stat().st_mtime
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text())
        if cached.get("source_mtime") == mtime:
            return {"lines": cached["lines"]}

    result = {"lines": classify_coastline(image_path), "source_mtime": mtime}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result))
    return {"lines": result["lines"]}


def _classify_sea_mask(image_path: Path):
    """Shared land/sea heuristic: classify per-pixel on blue-vs-red bias at
    a downscaled working resolution, then denoise with morphology. Returns
    (sea_mask_work, full_w, full_h, work_w, work_h).

    Our painted terrain art renders water in cool navy/teal tones and land
    in warm tan/green/brown. We classify on channel bias rather than
    region-growing from a "known sea" seed. Texture noise and haze over
    distant land make flood-fill unreliable here.

    This is a coarse heuristic, not per-pixel-exact."""
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    full_h, full_w = full.shape[:2]

    scale = min(1.0, COASTLINE_WORK_WIDTH / full_w)
    work_w, work_h = max(1, round(full_w * scale)), max(1, round(full_h * scale))
    work = cv2.resize(full, (work_w, work_h), interpolation=cv2.INTER_AREA)
    work = cv2.medianBlur(work, 5)

    blue = work[:, :, 0].astype(np.int16)
    red = work[:, :, 2].astype(np.int16)
    sea_mask = ((blue - red) > COASTLINE_BLUE_BIAS).astype(np.uint8) * 255

    kernel = np.ones((COASTLINE_MORPH_KERNEL, COASTLINE_MORPH_KERNEL), np.uint8)
    sea_mask = cv2.morphologyEx(sea_mask, cv2.MORPH_OPEN, kernel)
    sea_mask = cv2.morphologyEx(sea_mask, cv2.MORPH_CLOSE, kernel)

    return sea_mask, full_w, full_h, work_w, work_h


def build_land_mask(image_path: Path) -> np.ndarray:
    """Full-resolution boolean mask, True where image_path counts as land.
    Used by gapfill.fill_land_gaps to fill coastline gaps in the exported
    region raster. Same coarse heuristic as classify_coastline, just
    returned as a mask instead of traced into boundary lines."""
    sea_mask, full_w, full_h, _work_w, _work_h = _classify_sea_mask(image_path)
    sea_mask_full = cv2.resize(
        sea_mask, (full_w, full_h), interpolation=cv2.INTER_NEAREST
    )
    return sea_mask_full == 0


def classify_coastline(image_path: Path) -> list:
    """Classify land vs. sea in image_path and trace the boundary between
    them, for use as a snapping aid when drawing region borders.

    This is a coarse heuristic, not per-pixel-exact. It gives border
    snapping something to pull towards, not a precise terrain mask."""
    sea_mask, full_w, full_h, work_w, work_h = _classify_sea_mask(image_path)

    contours, _ = cv2.findContours(sea_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    scale_x, scale_y = full_w / work_w, full_h / work_h
    lines = []
    for contour in contours:
        if cv2.contourArea(contour) < COASTLINE_MIN_CONTOUR_AREA:
            continue
        simplified = cv2.approxPolyDP(contour, COASTLINE_SIMPLIFY_EPSILON, True)
        pts = [
            [round(float(pt[0][0]) * scale_x, 1), round(float(pt[0][1]) * scale_y, 1)]
            for pt in simplified
        ]
        if len(pts) >= 2:
            lines.append(pts)

    return lines


def export_project(project: dict, image_path: Path, out_dir: Path) -> dict:
    with Image.open(image_path) as src:
        size = src.size

    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)

    regions_by_color = {}
    for region in project.get("regions", []):
        name = region.get("name", "").strip()
        color = region.get("color", "#808080")
        if not name:
            continue
        for polygon in region.get("polygons", []):
            if len(polygon) < 3:
                continue
            pts = [(float(x), float(y)) for x, y in polygon]
            draw.polygon(pts, fill=color)
        regions_by_color[color] = name.replace(" ", "_")

    land_mask = build_land_mask(image_path)
    filled = fill_land_gaps(np.array(canvas), land_mask)
    canvas = Image.fromarray(filled)

    out_dir.mkdir(parents=True, exist_ok=True)
    region_map_path = out_dir / "region_map.png"
    regions_txt_path = out_dir / "regions.txt"

    canvas.save(region_map_path)
    regions_txt_path.write_text(
        json.dumps(regions_by_color, indent=1, ensure_ascii=False)
    )

    return {
        "region_map": str(region_map_path),
        "regions_txt": str(regions_txt_path),
        "region_count": len(regions_by_color),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default=str(GAME_DIR_DEFAULT / "backdrop.png"),
        help="Terrain image to trace borders on top of.",
    )
    parser.add_argument(
        "--dev-dir",
        default=str(DEV_DIR_DEFAULT),
        help="Dev output directory: draft project.json + exported region_map.png/regions.txt.",
    )
    parser.add_argument(
        "--game-dir",
        default=str(GAME_DIR_DEFAULT),
        help="Live game map_data directory, used only as a bootstrap-import fallback (never written).",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    handler = make_handler(args)
    server = ThreadingHTTPServer(("localhost", args.port), handler)
    print(f"Map editor running at http://localhost:{args.port}")
    print(f"  tracing over: {args.image}")
    print(f"  dev output:   {args.dev_dir}")
    print("  promote with: make promote-map (from repo root)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
