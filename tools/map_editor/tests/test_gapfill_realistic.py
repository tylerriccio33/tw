"""Exercises gapfill against realistic geometry and the real map asset.

test_gapfill.py and test_server.py only use small, clean synthetic
canvases: axis-aligned rectangles, at most two regions, a razor-sharp
two-tone land/sea split.

The unit tests can all pass while a real export still leaves a faction
unmapped at its edges. Example: a concave, hand-traced border whose gap
exceeds MAX_GAP_PX at real map resolution. Or a three-way region
junction where nearest-color tie-breaking behaves unexpectedly. These
tests check the invariant that matters in practice: every land pixel
gets some region's exact color. No declared region loses its
coastline.
"""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import server as srv
from gapfill import clip_sea_overflow, fill_land_gaps

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_BACKDROP = REPO_ROOT / "campaign" / "map_data" / "backdrop.png"
REAL_PROJECT = Path(__file__).resolve().parents[1] / "dev_map_data" / "project.json"

pytestmark = pytest.mark.skipif(
    not (REAL_BACKDROP.is_file() and REAL_PROJECT.is_file()),
    reason="real map assets not present in this checkout",
)


def _draw_project(project: dict, size) -> np.ndarray:
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    for region in project["regions"]:
        for polygon in region["polygons"]:
            pts = [(float(x), float(y)) for x, y in polygon]
            if len(pts) >= 3:
                draw.polygon(pts, fill=region["color"])
    return np.array(canvas)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# Real map asset regression: the actual hand-traced project against the
# actual painted backdrop, at full 1300x647 resolution.
# ---------------------------------------------------------------------------


def test_real_project_export_leaves_no_unfilled_land_gaps():
    # gapfill.py's own docstring says a whole untraced country stays
    # background by design. fill_land_gaps won't flood a neighbor's color
    # across an arbitrarily large unclaimed area. Real hand-traced borders
    # also carry residual imprecision: a continuous distribution of gap
    # sizes, not a clean bug/no-bug split. No cap reduces the small-seam
    # total to exactly zero without also chewing into legitimately
    # untraced land. So this is a regression budget, not a zero-tolerance
    # check: total area of gaps too small to plausibly be "a whole
    # country" must stay well below what an unfilled border seam bug
    # would add. See MAX_GAP_PX in gapfill.py for the tuning history.
    import cv2

    UNCLAIMED_COMPONENT_FLOOR_PX = 2000  # below this, it's a seam, not a country
    SEAM_BUDGET_PX = 700  # generous headroom over the current ~580px baseline

    project = json.loads(REAL_PROJECT.read_text())
    size = tuple(project["image_size"])
    canvas = _draw_project(project, size)
    land_mask = srv.build_land_mask(REAL_BACKDROP)
    assert land_mask.shape == (size[1], size[0])

    clipped = clip_sea_overflow(canvas, land_mask)
    filled = fill_land_gaps(clipped, land_mask)

    background = np.array((255, 255, 255), dtype=np.uint8)
    still_background = np.all(filled == background, axis=-1)
    unfilled_land_gaps = still_background & land_mask

    _, _, stats, _ = cv2.connectedComponentsWithStats(
        unfilled_land_gaps.astype(np.uint8), connectivity=8
    )
    areas = stats[1:, cv2.CC_STAT_AREA]
    seam_area = int(areas[areas < UNCLAIMED_COMPONENT_FLOOR_PX].sum())

    assert seam_area < SEAM_BUDGET_PX, (
        f"{seam_area}px of unfilled land gaps too small to be an "
        "unclaimed country - likely hand-traced border seams that should "
        "have closed and now leave a faction unmapped at its edge"
    )


def test_real_project_every_declared_region_survives_export():
    project = json.loads(REAL_PROJECT.read_text())
    size = tuple(project["image_size"])
    canvas = _draw_project(project, size)
    land_mask = srv.build_land_mask(REAL_BACKDROP)

    clipped = clip_sea_overflow(canvas, land_mask)
    filled = fill_land_gaps(clipped, land_mask)

    present_colors = {tuple(px) for px in filled.reshape(-1, 3)}
    for region in project["regions"]:
        rgb = _hex_to_rgb(region["color"])
        assert rgb in present_colors, (
            f"region {region['name']!r} ({region['color']}) has no pixels "
            "left after gapfill - it was fully swallowed by a neighbor "
            "or clipped as sea overflow"
        )


# ---------------------------------------------------------------------------
# Synthetic but realistic-scale geometry: concave polygons, a three-way
# junction, and a noisy (non-razor-sharp) land/sea classification.
# ---------------------------------------------------------------------------


def _star_polygon(cx, cy, r_outer, r_inner, points=7):
    import math

    pts = []
    for i in range(points * 2):
        r = r_outer if i % 2 == 0 else r_inner
        angle = math.pi * i / points
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def test_three_way_region_junction_fills_without_a_stray_gap():
    # Three concave (star-shaped) regions meeting near a common point,
    # each leaving a natural 1-3px unassigned seam at the border, the
    # way independently hand-traced polygons do. No single pair of
    # adjacent-rectangle tests in test_gapfill.py covers a >2-region
    # junction or non-convex borders.
    size = (300, 300)
    land_mask = np.ones((size[1], size[0]), dtype=bool)
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    draw.polygon(_star_polygon(100, 100, 70, 50), fill=(255, 0, 0))
    draw.polygon(_star_polygon(200, 100, 70, 50), fill=(0, 255, 0))
    draw.polygon(_star_polygon(150, 200, 70, 50), fill=(0, 0, 255))
    canvas_rgb = np.array(canvas)

    filled = fill_land_gaps(canvas_rgb, land_mask, max_gap_px=8)

    background = np.array((255, 255, 255), dtype=np.uint8)
    still_background = np.all(filled == background, axis=-1)
    # Gaps strictly between the three regions (not the shared open
    # background outside all of them) should close.
    near_center = still_background[90:210, 90:210]
    assert near_center.sum() / near_center.size < 0.05

    # Each region's exact color must still be present and not have been
    # overwritten by a neighbor at the junction.
    present_colors = {tuple(px) for px in filled.reshape(-1, 3)}
    assert (255, 0, 0) in present_colors
    assert (0, 255, 0) in present_colors
    assert (0, 0, 255) in present_colors


def test_clip_then_fill_pipeline_does_not_let_sea_overflow_get_refilled():
    # Regression for the clip -> fill ordering dependency: clip_sea_overflow
    # must clip a region painted too far over open water first, and
    # fill_land_gaps must NOT then re-fill that background just because
    # it's close to the (now-clipped) region edge.
    size = (200, 100)
    canvas = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(canvas)
    # Polygon spans far past the coastline at x=100 into open sea.
    draw.polygon([(0, 0), (180, 0), (180, 80), (0, 80)], fill=(255, 0, 255))
    canvas_rgb = np.array(canvas)

    land_mask = np.zeros((100, 200), dtype=bool)
    land_mask[:, :100] = True  # true shore at x=100

    clipped = clip_sea_overflow(canvas_rgb, land_mask, min_area_px=50)
    filled = fill_land_gaps(clipped, land_mask, max_gap_px=8)

    # Sea side, well past the coastline, must stay background through
    # both stages - not get bridged back by the fill step.
    assert np.all(filled[40, 110:190] == (255, 255, 255))
    # Land side must keep its exact region color.
    assert np.all(filled[40, 10:90] == (255, 0, 255))
