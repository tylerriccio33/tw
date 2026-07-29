"""Unit tests for gapfill's fill/clip functions, isolated from the exporter."""

import numpy as np
from gapfill import fill_land_gaps

WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)


def make_canvas(width=20, height=20, fill=WHITE):
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:, :] = fill
    return canvas


def test_fills_small_gap_with_neighboring_region_color():
    canvas = make_canvas()
    canvas[:, :10] = RED  # region covering the left half
    land_mask = np.ones((20, 20), dtype=bool)  # everything is land

    filled = fill_land_gaps(canvas, land_mask, max_gap_px=8)

    # A gap within max_gap_px of the region must take its exact color,
    # not stay white or blend.
    assert np.all(filled[:, 10:18] == RED)


def test_leaves_sea_background_untouched():
    canvas = make_canvas()
    canvas[:, :10] = RED
    land_mask = np.zeros((20, 20), dtype=bool)
    land_mask[:, :10] = True  # only the region side counts as land

    filled = fill_land_gaps(canvas, land_mask)

    assert np.all(filled[:, :10] == RED)
    assert np.all(filled[:, 10:] == WHITE)


def test_gap_between_two_regions_picks_nearest_exact_color():
    canvas = make_canvas(width=21)
    canvas[:, :10] = RED
    canvas[:, 11:] = GREEN
    # x=10 is an unassigned 1px gap between the two regions.
    land_mask = np.ones((20, 21), dtype=bool)

    filled = fill_land_gaps(canvas, land_mask)

    gap_colors = {tuple(px) for px in filled[:, 10]}
    assert gap_colors <= {RED, GREEN}
    assert np.all(filled[:, :10] == RED)
    assert np.all(filled[:, 11:] == GREEN)


def test_no_seed_pixels_returns_canvas_unchanged():
    canvas = make_canvas()  # entirely background, no region colors at all
    land_mask = np.ones((20, 20), dtype=bool)

    filled = fill_land_gaps(canvas, land_mask)

    assert np.array_equal(filled, canvas)


def test_no_gaps_returns_canvas_unchanged():
    canvas = make_canvas()
    canvas[:, :] = RED  # fully covered, nothing to fill
    land_mask = np.ones((20, 20), dtype=bool)

    filled = fill_land_gaps(canvas, land_mask)

    assert np.array_equal(filled, canvas)


def test_does_not_flood_a_large_untraced_land_area():
    # A whole untraced country (30px of background, all land) sits next to
    # one small colored region. This must NOT get annexed by that region's
    # color - only the strip within max_gap_px of it should fill.
    canvas = make_canvas(width=50)
    canvas[:, :5] = RED  # the only traced region
    land_mask = np.ones((20, 50), dtype=bool)

    filled = fill_land_gaps(canvas, land_mask, max_gap_px=8)

    assert np.all(filled[:, :5] == RED)
    assert np.all(filled[:, 14:] == WHITE)  # far side of the untraced country
    assert np.all(filled[:, 5:13] == RED)  # within max_gap_px, bridged


def test_does_not_mutate_input_canvas():
    canvas = make_canvas()
    canvas[:, :10] = RED
    original = canvas.copy()
    land_mask = np.ones((20, 20), dtype=bool)

    fill_land_gaps(canvas, land_mask)

    assert np.array_equal(canvas, original)
