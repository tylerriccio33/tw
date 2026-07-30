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

# build_land_mask classifies at full resolution instead of reusing the
# downscaled, box-blurred working copy above. The blur is what makes a
# flattened checkerboard readable, but averaging over DENOISE_KERNEL px
# also pulls the white/sea threshold roughly half a kernel *inland*: the
# mask came out ~15px inside the art's own shore all the way round, and
# small islands averaged down to sea and vanished outright. A coastline
# short of the shore leaves the backdrop's white land fill showing as a
# rim around every province, since provinces clip to this mask.
#
# Dropping the blur means white checkerboard cells in a flattened source
# would read as land again, so the defense moves to component size. Each
# such cell is an isolated island of roughly CHECKER_CELL_PX^2 (its
# same-color diagonal neighbors only touch at corners, so 4-connectivity
# keeps them separate), and this discards anything that small. Real land
# that tiny is a speck no province could hold.
CHECKER_CELL_PX = 15  # px, cell period of a baked-in transparency checkerboard
MIN_ISLAND_PX = 4 * CHECKER_CELL_PX**2  # px^2, smallest land component kept

# Line art draws a dark stroke along the shore, and the stroke isn't white
# so it isn't land. That leaves the mask a pixel ragged in both directions:
# where a strait or river mouth is drawn narrow the strokes from either
# bank meet and pinch the landmass to a single pixel of white, and a spit
# or a stroke's own anti-aliasing leaves 1px hairs sticking out.
#
# Either one is a ring no polygon tracer can express. A contour walked
# around a 1px pinch or hair traverses the same pixel twice, so the ring
# revisits a point - which is exactly what export refuses, because it
# fills as a pinched-shut shape rather than the area meant.
#
# A close then an open at the stroke's own width fixes both: the close
# bridges pinches, the open shaves hairs. Neither moves the shore, and
# together they leave every ring on this map traceable.
SMOOTH_KERNEL = 3  # px, closes 1px pinches then shaves 1px hairs

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
    """Full-resolution boolean mask, True where image_path counts as land.

    Land is the art's own white fill, at its own edges. See
    MIN_ISLAND_PX for why this reads the raw pixels rather than the
    blurred working copy _sea_mask builds.

    The dark outline stroke a line-art coast uses stays *out* of the
    mask. That is deliberate: it keeps a couple of shore pixels visible
    outside every province fill. That reads as a coastline, not a gap.
    """
    full = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if full is None:
        raise ValueError(f"Could not read image at {image_path}")
    gray = cv2.cvtColor(full, cv2.COLOR_BGR2GRAY)
    land = (gray >= LAND_MIN_GRAY).astype(np.uint8)

    kernel = np.ones((SMOOTH_KERNEL, SMOOTH_KERNEL), np.uint8)
    land = cv2.morphologyEx(land, cv2.MORPH_CLOSE, kernel)
    land = cv2.morphologyEx(land, cv2.MORPH_OPEN, kernel)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(land, connectivity=4)
    keep = np.zeros(count, dtype=bool)
    for label in range(1, count):
        keep[label] = stats[label, cv2.CC_STAT_AREA] >= MIN_ISLAND_PX
    return keep[labels]


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
