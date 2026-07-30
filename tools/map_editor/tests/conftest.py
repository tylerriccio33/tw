"""Shared fixtures for building small, real map packages.

Tests build packages through init_package's own manifest/layer_configs
rather than hand-rolling JSON. A config change that breaks the format
then fails here, not when someone next opens the editor.
"""

import json
from pathlib import Path

import cv2
import init_package
import mapfmt
import numpy as np
import pytest
from PIL import Image


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
        if cfg.input != "point":
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
