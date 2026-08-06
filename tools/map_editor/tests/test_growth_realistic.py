"""Full-scale regression for growth.py against the real campaign backdrop.

Regression: revectorize() traces each province's raster mask with
cv2.findContours every growth step. Two lobes of the same claim can end
up touching only at a single pixel corner. That happens when growth
wraps around an obstacle, or two fronts of the same city meet from
different directions. cv2.findContours traces that as a figure-eight
that revisits the corner pixel: a self-touching polygon. That's exactly
what export's validation rejects ("crosses itself" / province overlap).

A small synthetic map rarely has room for that geometry to occur. The
real backdrop's coastline reliably does. So this test drives growth to
completion on it and asserts the result validates cleanly.

Skips if the backdrop isn't present, matching test_realistic.py.
"""

import export
import growth
import init_package
import mapfmt
import numpy as np
import pytest

BACKDROP = init_package.GAME_DIR_DEFAULT / "backdrop.png"

pytestmark = pytest.mark.skipif(
    not BACKDROP.is_file(), reason=f"no backdrop at {BACKDROP}"
)


def test_growth_to_completion_produces_valid_geometry(tmp_path):
    root = tmp_path / "package"
    init_package.init_package(root, BACKDROP, province_count=12)
    package = mapfmt.load_package(root)
    project = mapfmt.load_project(root, package)

    mask = growth._land_mask(package, project)
    height, width = mask.shape
    rng = np.random.default_rng(0)
    points = {}
    tries = 0
    while len(points) < 15 and tries < 5000:
        tries += 1
        x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
        if mask[y, x]:
            i = len(points) + 1
            points[f"p{i}"] = {
                "x": float(x),
                "y": float(y),
                "tier": int(rng.integers(1, 6)),
                "name": f"city-{i}",
            }
    project.setdefault("layers", {}).setdefault(package.city_layer.name, {})[
        "points"
    ] = points

    growth.start(package, project)
    for _ in range(400):
        result = growth.step(package, project)
        if result["done"]:
            break

    assert export.validate_package(project, package) == []
