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
