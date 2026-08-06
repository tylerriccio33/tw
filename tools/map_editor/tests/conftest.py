"""Shared fixtures for building small, real map packages.

Tests build packages through init_package's own manifest/layer_configs
rather than hand-rolling JSON. A config change that breaks the format
then fails here, not when someone next opens the editor.
"""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import cv2
import init_package
import mapfmt
import numpy as np
import pytest
import server
from PIL import Image


def _live_server(package_dir: Path) -> ThreadingHTTPServer:
    """Start the real HTTP handler against `package_dir` on a background
    daemon thread. Tests use this to drive the editor over actual HTTP
    (or a real browser) instead of calling server.py's functions
    directly. Shut down with `httpd.shutdown()`."""
    handler = server.make_handler(package_dir)
    httpd = ThreadingHTTPServer(("localhost", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def write_backdrop(path: Path, size=(60, 40), land_box=(10, 8, 50, 32)) -> None:
    """Clean line-art backdrop: white land rectangle, dark sea around it.
    Matches what coastline.assert_clean_source insists on."""
    width, height = size
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (30, 30, 30)  # sea
    x0, y0, x1, y1 = land_box
    img[y0:y1, x0:x1] = (255, 255, 255)  # land
    cv2.imwrite(str(path), img)


def make_package(
    tmp_path: Path,
    *,
    size=(60, 40),
    land_box=(10, 8, 50, 32),
    extra_layers: dict | None = None,
) -> mapfmt.Package:
    """A package with the real five layers, plus any extra layer configs
    the caller passes in. Those join the manifest's layer order too."""
    root = tmp_path / "package"
    layers_dir = root / mapfmt.LAYERS_DIRNAME
    layers_dir.mkdir(parents=True)

    write_backdrop(root / "backdrop.png", size, land_box)

    man = init_package.manifest(size)
    configs = init_package.layer_configs()
    for name, cfg in (extra_layers or {}).items():
        configs[name] = cfg
        man["layers"].append(name)

    (root / mapfmt.MANIFEST_NAME).write_text(json.dumps(man))
    (root / "factions.json").write_text(json.dumps(init_package.FACTIONS))
    for name, cfg in configs.items():
        (layers_dir / f"{name}.json").write_text(json.dumps(cfg))

    blank = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    for name, cfg in configs.items():
        if cfg.get("input") == "brush":
            Image.fromarray(blank).save(layers_dir / cfg["raster"])

    return mapfmt.load_package(root)


def land_rect(land_box=(10, 8, 50, 32)) -> list[list[float]]:
    """The coastline polygon covering exactly the backdrop's white pixels.

    write_backdrop fills a half-open numpy slice, while PIL's polygon
    fill includes both endpoints. The polygon has to stop one pixel short,
    or the traced coastline claims a row of sea.
    """
    x0, y0, x1, y1 = land_box
    return [[x0, y0], [x1 - 1, y0], [x1 - 1, y1 - 1], [x0, y1 - 1]]


def _centroid(polygon: list[list[float]]) -> list[float]:
    """Vertex average. Test provinces are always convex, so this lands
    inside."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return [sum(xs) / len(xs), sum(ys) / len(ys)]


def project_with(
    package: mapfmt.Package,
    *,
    provinces: list[dict],
    land_box=(10, 8, 50, 32),
    assignments: dict | None = None,
) -> dict:
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [land_rect(land_box)]}
    ]
    project["layers"]["provinces"]["features"] = provinces
    project["layers"]["ownership"]["assignments"] = assignments or {}
    # Any point layer (e.g. cities) requires one authored point per
    # province to export. Tests care about their own layer, not capitals,
    # so seed every point layer with a point that's guaranteed to sit
    # inside its province.
    for name, cfg in package.layers.items():
        if cfg.input != "point" or cfg.point_coupling != "province":
            continue
        project["layers"][name]["points"] = {
            str(p["id"]): _centroid(p["polygons"][0])
            for p in provinces
            if p.get("polygons")
        }
    return project


def box(x0, y0, x1, y1) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def paint(package: mapfmt.Package, layer: str, hex_color: str, region) -> None:
    """Paint a rectangle into a brush layer's raster, the way the editor's
    brush would."""
    path = package.raster_path(layer)
    with Image.open(path) as im:
        raster = np.array(im.convert("RGB"))
    x0, y0, x1, y1 = region
    raster[y0:y1, x0:x1] = np.array(mapfmt.hex_to_rgb(hex_color), dtype=np.uint8)
    Image.fromarray(raster).save(path)


@pytest.fixture
def package(tmp_path):
    return make_package(tmp_path)


# --------------------------------------------------------------------------
# pinch fixture: a small, deliberately adversarial coastline for growth.py
# --------------------------------------------------------------------------
#
# test_growth_realistic.py exists because growth.py's revectorize() traces
# each province's raster claim with cv2.findContours every step, and two
# lobes of the *same* claim can end up touching at a single pixel corner -
# growth wrapping around an obstacle, or two fronts of the same city
# meeting from different directions. cv2.findContours traces that as a
# figure-eight that revisits the corner pixel: a self-touching polygon
# that export's validation rejects. A plain bigger rectangle (see
# test_growth.py's big_package) never reproduces it - there's nothing for
# a single claim to wrap around.
#
# This fixture is a much smaller coastline that still has that room: a
# rectangular landmass with a diamond-shaped lake (a "sea" feature
# painted over the land after it, so it reads as a hole rather than open
# ocean) plus a fjord notch cut in from the top, and a small detached
# island a few pixels off the mainland. A single low-tier city seeded
# just to the lake's left has to grow around both sides of it to reach
# the rest of the landmass, which is exactly the "wraps around an
# obstacle" case above. Running growth to completion on it confirms
# (not just assumes) that this matters - it prints
# "revectorize: provinces/<id> closed <n>px of self-touching pinch" from
# export.revectorize's pinch repair - i.e. the geometry actually drives a
# real claim into a diagonal-only self-touch, the same code path
# test_growth_realistic.py guards on the real backdrop.
PINCH_SIZE = (240, 160)
PINCH_LAND_BOX = (20, 20, 220, 140)  # bounding box, for write_backdrop only


def _diamond(cx: float, cy: float, r: float) -> list[list[float]]:
    return [[cx, cy - r], [cx + r, cy], [cx, cy + r], [cx - r, cy]]


def pinch_coastline_features() -> list[dict]:
    """A landmass with a lake obstacle, a fjord notch, and a detached
    island. See the module docstring above for why."""
    mainland = box(*PINCH_LAND_BOX)
    island = box(230, 20, 238, 35)  # a few px clear of the mainland's x=220 edge
    lake = _diamond(70, 80, 20)  # obstacle a city west of it must wrap around
    fjord = [[130, 20], [150, 20], [145, 100], [135, 100]]  # notch cut from the top
    return [
        {"key": "land", "polygons": [mainland, island]},
        {"key": "sea", "polygons": [lake]},
        {"key": "sea", "polygons": [fjord]},
    ]


@pytest.fixture
def pinch_package(tmp_path):
    """Package + project ready for a growth.py test.

    Adversarial coastline authored, two city points seeded. One sits
    right against the lake obstacle to force wraparound. The other sits
    elsewhere, so the map has more than one province. Caller drives
    growth.start() and growth.step() itself."""
    package = make_package(tmp_path, size=PINCH_SIZE, land_box=PINCH_LAND_BOX)
    project = mapfmt.empty_project(package.size, package)
    project["layers"]["coastline"]["features"] = pinch_coastline_features()
    project["layers"]["cities"]["points"] = {
        "p1": {"x": 30.0, "y": 80.0, "tier": 2, "name": "city-1"},
        "p2": {"x": 190.0, "y": 30.0, "tier": 3, "name": "city-2"},
    }
    return package, project
