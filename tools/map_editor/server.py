#!/usr/bin/env python3
"""Local map editor server.

Serves a browser-based tool for tracing faction/territory borders on top
of a clean line-art coastline map. Land is white fill, sea is a dark
outline stroke; see assert_clean_coastline_source. Everything the editor
writes goes to a *dev* location:
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
from gapfill import clip_sea_overflow, fill_land_gaps
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"
GAME_DIR_DEFAULT = REPO_ROOT / "campaign" / "map_data"

MIN_CONTOUR_AREA = 12  # px^2, drops single-pixel noise contours on import
SIMPLIFY_EPSILON = 1.2  # px, cv2.approxPolyDP tolerance on import

COASTLINE_WORK_WIDTH = 900  # px, classification runs on a downscaled copy for speed
COASTLINE_LAND_MIN_GRAY = 245  # min grayscale value for a pixel to count as land fill
# A source flattened from real transparency (e.g. a cached copy of a PNG
# viewed outside its original app) often bakes the checkerboard in as
# actual alternating-color pixels. A *median* blur is a no-op on a
# perfectly regular checkerboard at any kernel size - each cell is
# outvoted by its opposite-color neighbors, so the pattern survives
# untouched. A *box* blur averages instead of voting, so it collapses the
# checker into a uniform mid-gray that reads correctly as sea, while
# solid land fill (already uniform white) stays unaffected. Must exceed
# the checker's cell period (empirically ~15px here) to fully collapse it.
COASTLINE_DENOISE_KERNEL = 31  # px, box-blur size for collapsing checkerboard noise
COASTLINE_MORPH_KERNEL = 7  # px, smooths the sea mask and drops speckle before tracing
COASTLINE_MIN_CONTOUR_AREA = 120  # px^2 at working resolution, drops speckle contours
COASTLINE_SIMPLIFY_EPSILON = 2.0  # px at working resolution

# assert_clean_coastline_source: a real painted-terrain photo/render has a
# continuous spread of midtone pixels (mountains, shading, texture). Clean
# line art has almost none - just white fill, a dark outline stroke, and
# whatever color the sea uses. If more than this fraction of pixels land
# in the mid-gray band, the source probably shows painted terrain again,
# not the flat outline map this classifier expects.
COASTLINE_MIDTONE_MIN_GRAY = 60
COASTLINE_MIDTONE_MAX_GRAY = 200
COASTLINE_MAX_MIDTONE_FRACTION = 0.05


def make_handler(args: argparse.Namespace):
    image_path = Path(args.image).resolve()
    assert_clean_coastline_source(image_path)
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

        def _send_file(self, path: Path, content_type: str | None = None):
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
                except (ValueError, OSError) as exc:  # surface errors to the UI
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

        contours, _hierarchy = cv2.findContours(
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


def assert_clean_coastline_source(image_path: Path) -> None:
    """Enforce the backdrop is clean line-art: white land fill, a dark
    outline stroke, sea in any other color. Never a painted/photographic
    terrain render.

    No per-pixel rule can classify a painted terrain image reliably.
    Git history shows this used to be a blue-vs-red channel bias, then a
    hue band. Both mis-read real land as sea.

    A clean outline map sidesteps that entirely. Land is just "is this
    pixel white," which only holds if the source looks like line art.
    So this raises loudly instead of silently producing a bad mask.
    Called once at server/preview startup, not per-classification.
    """
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if full is None:
        raise ValueError(f"Could not read image at {image_path}")
    gray = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY)
    midtone = (gray >= COASTLINE_MIDTONE_MIN_GRAY) & (
        gray <= COASTLINE_MIDTONE_MAX_GRAY
    )
    midtone_fraction = float(midtone.mean())
    if midtone_fraction > COASTLINE_MAX_MIDTONE_FRACTION:
        raise ValueError(
            f"{image_path} doesn't look like a clean line-art coastline map: "
            f"{midtone_fraction:.0%} of pixels are mid-gray (expected white "
            f"land fill + a dark outline stroke, under "
            f"{COASTLINE_MAX_MIDTONE_FRACTION:.0%} midtone). Painted/textured "
            "terrain art can't be classified reliably here - trace over a "
            "flat white-fill/black-outline coastline drawing instead."
        )


def _classify_sea_mask(image_path: Path):
    """Shared land/sea heuristic: classify per-pixel on brightness at a
    downscaled working resolution, then denoise with morphology. Returns
    (sea_mask_work, full_w, full_h, work_w, work_h).

    The backdrop is clean line-art (see assert_clean_coastline_source):
    white land, a dark outline stroke, sea in any other color.
    Classification is just "is this pixel white" - no color-space
    heuristics needed.

    This is a coarse heuristic, not per-pixel-exact."""
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    full_h, full_w = full.shape[:2]

    # Denoise at full resolution, before downscaling - see
    # COASTLINE_DENOISE_KERNEL for why this has to be a box blur.
    denoised = cv2.blur(full, (COASTLINE_DENOISE_KERNEL, COASTLINE_DENOISE_KERNEL))

    scale = min(1.0, COASTLINE_WORK_WIDTH / full_w)
    work_w, work_h = max(1, round(full_w * scale)), max(1, round(full_h * scale))
    work = cv2.resize(denoised, (work_w, work_h), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.int16)
    sea_mask = (gray < COASTLINE_LAND_MIN_GRAY).astype(np.uint8) * 255

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


def _segments_properly_intersect(p1, p2, p3, p4) -> bool:
    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1 = orientation(p3, p4, p1)
    d2 = orientation(p3, p4, p2)
    d3 = orientation(p1, p2, p3)
    d4 = orientation(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0


def polygon_self_intersects(points: list[tuple[float, float]]) -> bool:
    """True if any two non-adjacent edges of the closed polygon cross.
    A self-crossing polygon fills unpredictably: PIL's scanline fill
    doesn't follow a torn/bowtie shape the way a human eye would. This
    is how a mistraced faction border ends up rendering as a
    disconnected fragment instead of the intended landmass."""
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex, not a crossing
            if _segments_properly_intersect(*edges[i], *edges[j]):
                return True
    return False


def validate_project(project: dict, size: tuple[int, int]) -> list[str]:
    """Invariants a region_map.png/regions.txt export depends on. A
    violation means the export misrepresents what was drawn: a dropped
    faction, a torn shape, or an off-map border. export_project
    refuses to write anything until these are clean."""
    width, height = size
    problems = []
    name_by_color: dict[str, str] = {}

    for region in project.get("regions", []):
        name = region.get("name", "").strip()
        if not name:
            continue
        color = region.get("color", "#808080").strip().lower()

        prior_name = name_by_color.get(color)
        if prior_name is not None and prior_name != name:
            problems.append(
                f"'{name}' and '{prior_name}' both use color {color} - "
                "regions.txt maps one name per color, so one of them "
                "will be silently dropped from the exported map"
            )
        else:
            name_by_color[color] = name

        for poly_idx, polygon in enumerate(region.get("polygons", [])):
            if len(polygon) < 3:
                continue
            pts = [(float(x), float(y)) for x, y in polygon]

            out_of_bounds = [
                (x, y) for x, y in pts if not (0 <= x <= width and 0 <= y <= height)
            ]
            if out_of_bounds:
                x, y = out_of_bounds[0]
                problems.append(
                    f"'{name}' polygon {poly_idx} has a point "
                    f"({x:.1f}, {y:.1f}) outside the {width}x{height} map"
                )

            if len(set(pts)) < len(pts):
                problems.append(
                    f"'{name}' polygon {poly_idx} revisits the same point "
                    "twice - that pinches the shape into a self-touching "
                    "loop instead of the intended territory"
                )
            elif polygon_self_intersects(pts):
                problems.append(
                    f"'{name}' polygon {poly_idx} crosses itself - it will "
                    "render as a torn or disconnected shape instead of the "
                    "intended territory"
                )

    return problems


def export_project(project: dict, image_path: Path, out_dir: Path) -> dict:
    with Image.open(image_path) as src:
        size = src.size

    problems = validate_project(project, size)
    if problems:
        raise ValueError(
            "Export blocked, the map data doesn't make sense:\n"
            + "\n".join(f"- {p}" for p in problems)
        )

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
    clipped = clip_sea_overflow(np.array(canvas), land_mask)
    filled = fill_land_gaps(clipped, land_mask)
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
