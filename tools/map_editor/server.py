#!/usr/bin/env python3
"""Local map editor server.

Serves a browser tool for authoring a layered map package. Everything it
reads and writes lives in one package directory, by default
tools/map_editor/dev_map_data/. That is never the live game data, so you
can iterate freely. Run `make promote-map` once you're happy with an
export.

The server is deliberately thin. It knows how to read and write a
package, rasterize a layer, and run the gap-fill. It has no idea what any
particular layer *means*. The browser builds its entire UI from
/api/manifest. Add a layer to map.json and it appears, with no change
here or in the JavaScript.

Usage:
    cd tools/map_editor
    uv run server.py

Then open http://localhost:8765.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import coastline as coast
import export
import growth
import init_package
import mapfmt
import numpy as np
import roads
from gapfill import fill_land_gaps
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"


class ApiError(Exception):
    """Something the user did wrong, reported to the UI as a sentence
    rather than a stack trace."""


# --------------------------------------------------------------------------
# operations the routes delegate to
# --------------------------------------------------------------------------


def manifest_payload(package: mapfmt.Package) -> dict:
    """Everything the browser needs to build its UI. One request, because
    the UI can't render anything meaningful until it has all of it."""
    return {
        "size": list(package.size),
        "layer_order": package.layer_order,
        "province_layer": package.manifest["province_layer"],
        "city_layer": package.manifest.get("city_layer"),
        "road_layer": package.manifest.get("road_layer"),
        "has_reference": package.reference_path is not None,
        "factions": package.factions,
        "layers": {
            name: {
                "name": cfg.name,
                "title": cfg.title,
                "input": cfg.input,
                "kind": cfg.kind,
                "raster": cfg.raster,
                "legend": cfg.legend,
                "nodata_color": cfg.nodata_color,
                "default_key": cfg.default_key,
                "snap_source": cfg.snap_source,
                "clip_to": cfg.clip_to,
                "gapfill": cfg.gapfill,
                "point_coupling": cfg.point_coupling,
                "point_fields": cfg.point_fields,
                # Which layers this one may snap to: everything drawn
                # before it, since that's what already exists when you
                # start drawing on this one.
                "snap_candidates": [
                    other
                    for other in package.layers_before(name)
                    if package.layers[other].snap_source
                ],
            }
            for name, cfg in package.layers.items()
        },
    }


def quantize_to_legend(raster: np.ndarray, cfg: mapfmt.LayerConfig) -> np.ndarray:
    """Snap every pixel to an exact legend color.

    The browser paints with an antialiased canvas. A brush stroke comes
    back with a fringe of blended colors around its edge. No legend holds
    those, so they would reduce to nothing and render as noise. They also
    break the format's one hard rule: a pixel's color *is* its meaning,
    matched exactly. Snapping to the nearest legend entry keeps the
    stroke's shape and throws away the fringe.
    """
    palette_hex = [cfg.nodata_color] + list(cfg.legend.keys())
    # int32, not int16: a squared channel difference reaches 195075 across
    # three channels, which silently wraps in int16 and sends every pixel
    # to an arbitrary palette entry.
    palette = np.array([mapfmt.hex_to_rgb(h) for h in palette_hex], dtype=np.int32)

    flat = raster.reshape(-1, 3).astype(np.int32)
    # One (pixels, palette, channels) broadcast array would be huge at map
    # resolution (tens of millions of pixels x palette size x 3 x 4 bytes
    # easily hits multiple GB and OOM-kills the server) - loop over the
    # small palette instead, keeping every intermediate O(pixels).
    best_dist = np.full(flat.shape[0], np.iinfo(np.int32).max, dtype=np.int32)
    nearest = np.zeros(flat.shape[0], dtype=np.intp)
    for i, color in enumerate(palette):
        dist = ((flat - color) ** 2).sum(axis=1)
        better = dist < best_dist
        best_dist[better] = dist[better]
        nearest[better] = i
    return palette[nearest].astype(np.uint8).reshape(raster.shape)


def fill_gaps(project: dict, package: mapfmt.Package, layer_name: str) -> dict:
    """Run the export-time clip and gap-fill for one layer, and hand back
    the result as editable geometry.

    The point is that you see the fix as polygons you can keep editing.
    You throw it away by not saving. The alternative is a silent fix at
    export time that surprises you in the game.
    """
    cfg = package.layers.get(layer_name)
    if cfg is None:
        raise ApiError(f"no layer named '{layer_name}'")
    if cfg.input != "polygon":
        raise ApiError(
            f"'{layer_name}' is a {cfg.input} layer - gap-filling returns "
            "polygons, so it only applies to traced layers"
        )

    size = package.size
    masks: dict[str, np.ndarray] = {}
    for name in package.layer_order:
        other = package.layers[name]
        if other.kind != "mask":
            continue
        raster = export.rasterize_layer(project, package, other, size, None)
        for hex_color, entry in other.legend.items():
            rgb = np.array(mapfmt.hex_to_rgb(hex_color), dtype=np.uint8)
            masks[f"{name}:{entry['key']}"] = np.all(raster == rgb, axis=-1)
        if name == layer_name:
            break

    raster = export.rasterize_layer(project, package, cfg, size, None)
    before = raster.copy()

    if cfg.clip_to:
        raster = raster.copy()
        raster[~masks[cfg.clip_to]] = np.array(cfg.nodata_rgb, dtype=np.uint8)
    if cfg.gapfill:
        raster = fill_land_gaps(
            raster,
            masks[cfg.gapfill["within"]],
            cfg.nodata_rgb,
            cfg.gapfill.get("max_gap_px", 12),
        )

    nodata = np.array(cfg.nodata_rgb, dtype=np.uint8)
    changed = int(np.any(before != raster, axis=-1).sum())

    # What's still unclaimed inside the mask is a gap too wide for the
    # fill to bridge - i.e. land nobody has drawn yet. That's the half
    # worth telling the user about, since it needs a real decision.
    residual = 0
    if cfg.gapfill:
        within = masks[cfg.gapfill["within"]]
        residual = int((np.all(raster == nodata, axis=-1) & within).sum())

    return {
        "features": export.revectorize(raster, cfg, project),
        "changed_px": changed,
        "residual_px": residual,
        "max_gap_px": (cfg.gapfill or {}).get("max_gap_px"),
    }


def clean_shapes(project: dict, package: mapfmt.Package, layer_name: str) -> dict:
    """Resolve self-crossing and overlapping province polygons.

    Rasterizes the layer as export would - later features win contested
    pixels, same as export does silently. Then re-traces clean polygons
    from that raster. A traced contour can't be self-crossing or repeat
    a point. One id per pixel resolves the overlaps. Returns editable
    polygons rather than saving, so this is a preview you can inspect
    before keeping.
    """
    cfg = package.layers.get(layer_name)
    if cfg is None:
        raise ApiError(f"no layer named '{layer_name}'")
    if cfg.input != "polygon":
        raise ApiError(
            f"'{layer_name}' is a {cfg.input} layer - this only applies to "
            "traced layers"
        )

    size = package.size
    before = export.rasterize_polygon_layer(project, cfg, size)
    features = export.revectorize(before, cfg, project)
    after_project = {"layers": {layer_name: {"features": features}}}
    after = export.rasterize_polygon_layer(after_project, cfg, size)
    changed = int(np.any(before != after, axis=-1).sum())

    return {"features": features, "changed_px": changed}


FACTION_KEY_RE = re.compile(r"[a-z0-9_]+")


def set_factions(package: mapfmt.Package, project: dict, factions: list) -> list[dict]:
    """Replace the faction roster and keep every "assign"-input layer's
    legend in lockstep with it. This is the only way to add, rename, or
    delete a starting owner. An assign layer's legend is always just the
    roster, read back as paint-by-province categories.

    Assignments naming a faction that no longer exists get dropped,
    since export rejects a key outside its layer's own legend.
    """
    if not isinstance(factions, list) or not factions:
        raise ApiError("factions must be a non-empty list")

    seen_keys: set[str] = set()
    seen_colors: set[str] = set()
    cleaned: list[dict] = []
    for entry in factions:
        if not isinstance(entry, dict):
            raise ApiError("each faction must be an object")
        key = str(entry.get("key", "")).strip().lower()
        name = str(entry.get("name", "")).strip()
        color = str(entry.get("color", "")).strip().lower()
        if not key or not FACTION_KEY_RE.fullmatch(key):
            raise ApiError(
                f"faction key {key!r} must be lowercase letters, digits, underscores"
            )
        if not name:
            raise ApiError(f"faction '{key}' needs a name")
        mapfmt.hex_to_rgb(color)
        if key in seen_keys:
            raise ApiError(f"duplicate faction key '{key}'")
        if color in seen_colors:
            raise ApiError(f"two factions share the color {color}")
        seen_keys.add(key)
        seen_colors.add(color)
        cleaned.append({**entry, "key": key, "name": name, "color": color})

    factions_path = package.root / package.manifest.get("factions", "factions.json")
    factions_path.write_text(json.dumps(cleaned, indent=1) + "\n")

    for name, cfg in package.layers.items():
        if cfg.input != "assign":
            continue
        raw = dict(cfg.raw)
        raw["legend"] = {
            f["color"]: {"key": f["key"], "name": f["name"]} for f in cleaned
        }
        cfg_path = package.root / mapfmt.LAYERS_DIRNAME / f"{name}.json"
        cfg_path.write_text(json.dumps(raw, indent=1) + "\n")

        assignments = mapfmt.project_assignments(project, name)
        pruned = {pid: fkey for pid, fkey in assignments.items() if fkey in seen_keys}
        project.setdefault("layers", {}).setdefault(name, {})["assignments"] = pruned

    mapfmt.save_project(package.root, project)
    return cleaned


def autotrace(package: mapfmt.Package, layer_name: str) -> list[dict]:
    cfg = package.layers.get(layer_name)
    if cfg is None or cfg.kind != "mask":
        raise ApiError("autotrace only applies to a mask layer like the coastline")
    land_mask = coast.build_land_mask(package.backdrop_path)
    return init_package.trace_land_features(land_mask)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def make_handler(package_dir: Path):
    package_dir = Path(package_dir).resolve()

    def load():
        """Reload from disk per request. The package is small, and a hand-
        edited layer config then shows up on refresh, with no server
        restart."""
        package = mapfmt.load_package(package_dir)
        return package, mapfmt.load_project(package_dir, package)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass  # keep stdout quiet

        # -- plumbing ---------------------------------------------------

        def _send_json(self, obj, status=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, data: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path: Path, content_type: str | None = None):
            if not path.is_file():
                self.send_error(404, "Not found")
                return
            ctype = (
                content_type
                or mimetypes.guess_type(str(path))[0]
                or "application/octet-stream"
            )
            self._send_bytes(path.read_bytes(), ctype)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(length) if length else b""

        def _json_body(self) -> dict:
            raw = self._body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        # -- routes -----------------------------------------------------

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    self._send_file(STATIC_DIR / "index.html", "text/html")
                elif path.startswith("/static/"):
                    self._send_file(STATIC_DIR / path[len("/static/") :])
                elif path == "/api/manifest":
                    package, _ = load()
                    self._send_json(manifest_payload(package))
                elif path == "/api/project":
                    _, project = load()
                    self._send_json(project)
                elif path.startswith("/api/layer/") and path.endswith("/points"):
                    package, project = load()
                    name = path[len("/api/layer/") : -len("/points")]
                    if name not in package.layers:
                        self.send_error(404, "no such layer")
                        return
                    self._send_json(mapfmt.project_points(project, name))
                elif path == "/api/backdrop":
                    package, _ = load()
                    self._send_file(package.backdrop_path)
                elif path == "/api/reference":
                    package, _ = load()
                    if package.reference_path is None:
                        self.send_error(404, "no reference image configured")
                        return
                    self._send_file(package.reference_path)
                elif path.startswith("/api/layer/"):
                    package, _ = load()
                    name = path[len("/api/layer/") :].removesuffix(".png")
                    if name not in package.layers:
                        self.send_error(404, "no such layer")
                        return
                    raster = package.raster_path(name)
                    if not raster.is_file():
                        width, height = package.size
                        blank = np.zeros((height, width, 3), dtype=np.uint8)
                        blank[:] = np.array(
                            package.layers[name].nodata_rgb, dtype=np.uint8
                        )
                        buffer = BytesIO()
                        Image.fromarray(blank).save(buffer, format="PNG")
                        self._send_bytes(buffer.getvalue(), "image/png")
                    else:
                        self._send_file(raster, "image/png")
                else:
                    self.send_error(404, "Not found")
            except mapfmt.PackageError as exc:
                self._send_json({"error": str(exc)}, 500)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                if path == "/api/project":
                    mapfmt.save_project(package_dir, self._json_body())
                    self._send_json({"ok": True})

                elif path.startswith("/api/layer/") and path.endswith("/points"):
                    package, project = load()
                    name = path[len("/api/layer/") : -len("/points")]
                    if name not in package.layers:
                        raise ApiError(f"no layer named '{name}'")
                    if package.layers[name].input != "point":
                        raise ApiError(f"'{name}' is not a point layer")
                    payload = self._json_body()
                    points = project.setdefault("layers", {}).setdefault(name, {})
                    existing = points.setdefault("points", {})
                    existing.update(payload)
                    mapfmt.save_project(package_dir, project)
                    self._send_json({"ok": True, "points": existing})

                elif path.startswith("/api/layer/"):
                    package, _ = load()
                    name = path[len("/api/layer/") :].removesuffix(".png")
                    if name not in package.layers:
                        raise ApiError(f"no layer named '{name}'")
                    raster_path = package.raster_path(name)
                    raster_path.parent.mkdir(parents=True, exist_ok=True)
                    with Image.open(BytesIO(self._body())) as im:
                        raster = np.array(im.convert("RGB"))
                    raster = quantize_to_legend(raster, package.layers[name])
                    Image.fromarray(raster).save(raster_path)
                    self._send_json({"ok": True})

                elif path.startswith("/api/autotrace/"):
                    package, _ = load()
                    features = autotrace(package, path[len("/api/autotrace/") :])
                    self._send_json({"ok": True, "features": features})

                elif path == "/api/fillgaps":
                    package, project = load()
                    payload = self._json_body()
                    # Use the project the browser has in hand, not the last
                    # autosave - otherwise the fill runs against stale
                    # geometry and silently undoes recent edits.
                    result = fill_gaps(
                        payload.get("project") or project,
                        package,
                        payload.get("layer", ""),
                    )
                    self._send_json({"ok": True, **result})

                elif path == "/api/cleanshapes":
                    package, project = load()
                    payload = self._json_body()
                    result = clean_shapes(
                        payload.get("project") or project,
                        package,
                        payload.get("layer", ""),
                    )
                    self._send_json({"ok": True, **result})

                elif path == "/api/factions":
                    package, project = load()
                    payload = self._json_body()
                    cleaned = set_factions(package, project, payload.get("factions"))
                    self._send_json({"ok": True, "factions": cleaned})

                elif path == "/api/export":
                    package, project = load()
                    payload = self._json_body()
                    project = payload.get("project") or project
                    mapfmt.save_project(package_dir, project)
                    result = export.export_package(project, package)
                    self._send_json({"ok": True, **result})

                elif path == "/api/grow/start":
                    package, project = load()
                    payload = self._json_body()
                    project = payload.get("project") or project
                    result = growth.start(package, project)
                    mapfmt.save_project(package_dir, project)
                    self._send_json({"ok": True, **result})

                elif path == "/api/grow/step":
                    package, project = load()
                    payload = self._json_body()
                    project = payload.get("project") or project
                    result = growth.step(package, project)
                    mapfmt.save_project(package_dir, project)
                    self._send_json({"ok": True, **result})

                elif path == "/api/roads/start":
                    package, project = load()
                    payload = self._json_body()
                    project = payload.get("project") or project
                    result = roads.start(package, project)
                    mapfmt.save_project(package_dir, project)
                    self._send_json({"ok": True, **result})

                elif path == "/api/roads/step":
                    package, project = load()
                    payload = self._json_body()
                    project = payload.get("project") or project
                    result = roads.step(package, project)
                    mapfmt.save_project(package_dir, project)
                    self._send_json({"ok": True, **result})

                else:
                    self.send_error(404, "Not found")

            except (
                ApiError,
                export.ExportBlocked,
                mapfmt.PackageError,
                growth.GrowthError,
                roads.RoadsError,
            ) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
            except (ValueError, OSError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        default=str(DEV_DIR_DEFAULT),
        help="Map package to edit. Created with `make map-package-init`.",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    try:
        package = mapfmt.load_package(package_dir)
    except mapfmt.PackageError as exc:
        raise SystemExit(f"{exc}\n\nCreate one first:  make map-package-init SEED=12")

    handler = make_handler(package_dir)
    server = ThreadingHTTPServer(("localhost", args.port), handler)
    print(f"Map editor running at http://localhost:{args.port}")
    print(f"  package: {package_dir}")
    print(f"  layers:  {', '.join(package.layer_order)}")
    print("  promote with: make promote-map (from repo root)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
