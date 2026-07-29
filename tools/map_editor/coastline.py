"""Land/sea classification of a line-art backdrop.

This is bootstrap-only. init_package.py runs it for a fresh package, to
spare you clicking around a whole continent by hand. The editor runs it
again when you ask for a re-autotrace.

After that, the coastline counts as authored data. It lives in the
coastline layer, you edit it, and every other layer clips and snaps to
it. Nothing re-guesses it from pixel brightness again.

That distinction matters. The earlier pipeline derived the land mask
from the backdrop on every export. A border's meaning then hung on a
brightness heuristic nobody could see or correct.
"""

from pathlib import Path

import cv2
import numpy as np

WORK_WIDTH = 900  # px, classification runs on a downscaled copy for speed
LAND_MIN_GRAY = 245  # min grayscale value for a pixel to count as land fill

# A source flattened from real transparency (e.g. a cached copy of a PNG
# viewed outside its original app) often bakes the checkerboard in as
# actual alternating-color pixels. A *median* blur is a no-op on a
# perfectly regular checkerboard at any kernel size - each cell is
# outvoted by its opposite-color neighbors, so the pattern survives
# untouched. A *box* blur averages instead of voting, so it collapses the
# checker into a uniform mid-gray that reads correctly as sea, while
# solid land fill (already uniform white) stays unaffected. Must exceed
# the checker's cell period (empirically ~15px here) to fully collapse it.
DENOISE_KERNEL = 31  # px, box-blur size for collapsing checkerboard noise
MORPH_KERNEL = 7  # px, smooths the sea mask and drops speckle before tracing
MIN_CONTOUR_AREA = 120  # px^2 at working resolution, drops speckle contours
SIMPLIFY_EPSILON = 2.0  # px at working resolution

# assert_clean_source: a real painted-terrain photo/render has a
# continuous spread of midtone pixels (mountains, shading, texture). Clean
# line art has almost none - just white fill, a dark outline stroke, and
# whatever color the sea uses. If more than this fraction of pixels land
# in the mid-gray band, the source probably shows painted terrain again,
# not the flat outline map this classifier expects.
MIDTONE_MIN_GRAY = 60
MIDTONE_MAX_GRAY = 200
MAX_MIDTONE_FRACTION = 0.05


def assert_clean_source(image_path: Path) -> None:
    """Enforce the backdrop is clean line-art: white land fill, a dark
    outline stroke, sea in any other color. Never a painted/photographic
    terrain render.

    No per-pixel rule can classify a painted terrain image reliably.
    Git history shows this used to be a blue-vs-red channel bias, then a
    hue band. Both mis-read real land as sea.

    A clean outline map sidesteps that entirely. Land is just "is this
    pixel white," which only holds if the source looks like line art.
    So this raises loudly instead of silently producing a bad mask.
    """
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if full is None:
        raise ValueError(f"Could not read image at {image_path}")
    gray = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY)
    midtone = (gray >= MIDTONE_MIN_GRAY) & (gray <= MIDTONE_MAX_GRAY)
    midtone_fraction = float(midtone.mean())
    if midtone_fraction > MAX_MIDTONE_FRACTION:
        raise ValueError(
            f"{image_path} doesn't look like a clean line-art coastline map: "
            f"{midtone_fraction:.0%} of pixels are mid-gray (expected white "
            f"land fill + a dark outline stroke, under "
            f"{MAX_MIDTONE_FRACTION:.0%} midtone). Painted/textured terrain "
            "art can't be classified reliably here - trace over a flat "
            "white-fill/black-outline coastline drawing instead."
        )


def _sea_mask(image_path: Path):
    """Classify per-pixel on brightness at a downscaled working
    resolution, then denoise with morphology. Returns
    (sea_mask_work, full_w, full_h, work_w, work_h).

    A coarse heuristic, not per-pixel-exact - which is fine, because its
    output is a starting outline the user then edits."""
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    full_h, full_w = full.shape[:2]

    # Denoise at full resolution, before downscaling - see DENOISE_KERNEL
    # for why this has to be a box blur.
    denoised = cv2.blur(full, (DENOISE_KERNEL, DENOISE_KERNEL))

    scale = min(1.0, WORK_WIDTH / full_w)
    work_w, work_h = max(1, round(full_w * scale)), max(1, round(full_h * scale))
    work = cv2.resize(denoised, (work_w, work_h), interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.int16)
    sea = (gray < LAND_MIN_GRAY).astype(np.uint8) * 255

    kernel = np.ones((MORPH_KERNEL, MORPH_KERNEL), np.uint8)
    sea = cv2.morphologyEx(sea, cv2.MORPH_OPEN, kernel)
    sea = cv2.morphologyEx(sea, cv2.MORPH_CLOSE, kernel)

    return sea, full_w, full_h, work_w, work_h


def build_land_mask(image_path: Path) -> np.ndarray:
    """Full-resolution boolean mask, True where image_path counts as land."""
    sea, full_w, full_h, _work_w, _work_h = _sea_mask(image_path)
    sea_full = cv2.resize(sea, (full_w, full_h), interpolation=cv2.INTER_NEAREST)
    return sea_full == 0


def trace_lines(image_path: Path) -> list:
    """The land/sea boundary as polylines in the image's full-resolution
    pixel space."""
    sea, full_w, full_h, work_w, work_h = _sea_mask(image_path)

    contours, _ = cv2.findContours(sea, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    scale_x, scale_y = full_w / work_w, full_h / work_h
    lines = []
    for contour in contours:
        if cv2.contourArea(contour) < MIN_CONTOUR_AREA:
            continue
        simplified = cv2.approxPolyDP(contour, SIMPLIFY_EPSILON, True)
        pts = [
            [round(float(pt[0][0]) * scale_x, 1), round(float(pt[0][1]) * scale_y, 1)]
            for pt in simplified
        ]
        if len(pts) >= 2:
            lines.append(pts)

    return lines
