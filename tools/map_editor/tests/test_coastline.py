"""Land/sea classification of a line-art backdrop.

This only bootstraps the coastline layer - once traced, the coastline
counts as authored data. These tests guard the quality of that starting
outline, and the check that refuses art it can't read.
"""

import coastline
import cv2
import numpy as np
import pytest


def make_two_tone_image(path, width=200, height=100):
    """Left half white land fill, right half dark sea, matching the clean
    line-art convention assert_clean_source insists on."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, : width // 2] = (255, 255, 255)
    img[:, width // 2 :] = (40, 40, 40)
    cv2.imwrite(str(path), img)


def test_trace_lines_follows_the_land_sea_boundary(tmp_path):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    lines = coastline.trace_lines(image_path)

    assert lines, "expected at least one traced boundary"
    xs = [pt[0] for line in lines for pt in line]
    # The true split is at x=100; traced points should cluster near it
    # rather than at the canvas edges.
    assert any(80 <= x <= 120 for x in xs)


def test_build_land_mask_matches_known_land_and_sea_sides(tmp_path):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    land_mask = coastline.build_land_mask(image_path)

    assert land_mask.shape == (100, 200)
    assert land_mask.dtype == bool
    assert land_mask[50, 20], "well inside the white land half"
    assert not land_mask[50, 180], "well inside the dark sea half"


def test_build_land_mask_reaches_the_drawn_shore(tmp_path):
    """The mask has to end where the white fill ends.

    The classifier used to run on a 31px box-blurred copy, which pulled
    the threshold ~15px inland all the way round. Provinces clip to this
    mask. Every one of those pixels then showed up in-game as the
    backdrop's white land fill, ringing the territory.
    """
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    land_mask = coastline.build_land_mask(image_path)

    assert land_mask[50, 99], "the last white column is land"
    assert not land_mask[50, 100], "the first dark column is not"
    assert land_mask[:, :100].all(), "every white pixel is land"


def test_build_land_mask_keeps_small_islands(tmp_path):
    """An offshore island is land, however small. The blurred classifier
    averaged little ones down into sea and lost them."""
    image_path = tmp_path / "island.png"
    img = np.full((300, 300, 3), 40, dtype=np.uint8)
    img[100:150, 100:150] = (255, 255, 255)  # 50x50, well over MIN_ISLAND_PX
    cv2.imwrite(str(image_path), img)

    land_mask = coastline.build_land_mask(image_path)

    assert land_mask[125, 125], "the island is land"
    assert land_mask.sum() == 50 * 50


def test_build_land_mask_collapses_a_flattened_checkerboard(tmp_path):
    """A source flattened from real transparency bakes the checkerboard in
    as alternating cells. Its white cells are as bright as land fill.
    Only their size tells them apart - each is a speck, not a landmass."""
    image_path = tmp_path / "checker.png"
    img = np.full((300, 300, 3), 40, dtype=np.uint8)
    cell = coastline.CHECKER_CELL_PX
    for y in range(0, 300, cell):
        for x in range(0, 300, cell):
            if (y // cell + x // cell) % 2 == 0:
                img[y : y + cell, x : x + cell] = (255, 255, 255)
    cv2.imwrite(str(image_path), img)

    land_mask = coastline.build_land_mask(image_path)

    assert not land_mask.any(), "a checkerboard is not a continent"


def test_painted_terrain_art_is_refused_rather_than_misclassified(tmp_path):
    """The heuristic can only read flat line art. Given a painted terrain
    render it would produce a confident, wrong mask - so it refuses."""
    image_path = tmp_path / "painted.png"
    rng = np.random.default_rng(0)
    noisy = rng.integers(60, 200, size=(100, 200, 3), dtype=np.uint8)
    cv2.imwrite(str(image_path), noisy)

    with pytest.raises(ValueError, match="clean line-art"):
        coastline.assert_clean_source(image_path)


def test_clean_line_art_passes_the_guard(tmp_path):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path)
    coastline.assert_clean_source(image_path)  # must not raise


def test_trace_lines_drops_speckle_contours_below_the_area_floor(tmp_path, monkeypatch):
    """A tiny sea speck under MIN_CONTOUR_AREA shouldn't turn into a traced
    line - it's noise, not a coastline. Faked at the _sea_mask seam since
    the real image pipeline's blur makes a speck this small hard to
    produce deterministically."""
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    mask = np.zeros((100, 200), dtype=np.uint8)
    mask[10:80, 110:190] = 255  # big contour, kept
    mask[5:8, 5:8] = 255  # 3x3=9px^2 speck, well under MIN_CONTOUR_AREA

    monkeypatch.setattr(coastline, "_sea_mask", lambda path: (mask, 200, 100, 200, 100))

    lines = coastline.trace_lines(image_path)

    assert len(lines) == 1


def test_assert_clean_source_rejects_an_unreadable_image(tmp_path):
    bogus = tmp_path / "not-an-image.png"
    bogus.write_bytes(b"not a png at all")
    with pytest.raises(ValueError, match="Could not read image"):
        coastline.assert_clean_source(bogus)


def test_build_land_mask_rejects_an_unreadable_image(tmp_path):
    bogus = tmp_path / "not-an-image.png"
    bogus.write_bytes(b"not a png at all")
    with pytest.raises(ValueError, match="Could not read image"):
        coastline.build_land_mask(bogus)
