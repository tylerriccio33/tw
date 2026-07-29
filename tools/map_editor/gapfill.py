"""Closes small unclaimed strips left inside a layer's own mask.

A polygon edge can stop just short of the shore. Two borders traced
separately can leave a seam between them. Either way, background pixels
survive on land. fill_land_gaps() hands each one to its nearest
neighbor.

Colors stay exact, never blended. Every consumer of these rasters
matches colors exactly - a province id, a legend entry. A blended pixel
is not a compromise between two regions; it means nothing at all.

Only gaps up to max_gap_px get bridged. An untraced province can be an
arbitrarily large patch of background. Flooding a neighbor's color
across all of it would silently annex land nobody drew.

Clipping a layer to its mask is the opposite direction. That lives in
export.py, where the authored coastline makes it exact rather than a
heuristic.
"""

import cv2
import numpy as np

MAX_GAP_PX = 12  # widest untraced strip a gap-fill will bridge


def fill_land_gaps(
    canvas_rgb: np.ndarray,
    land_mask: np.ndarray,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    max_gap_px: float = MAX_GAP_PX,
) -> np.ndarray:
    """Return a copy of canvas_rgb with background pixels on land_mask
    replaced by their nearest region's color, within max_gap_px.
    Background off land_mask, or too far from any region, stays
    as-is."""
    background = np.array(background_rgb, dtype=np.uint8)
    background_mask = np.all(canvas_rgb == background, axis=-1)
    seed_mask = ~background_mask
    if not background_mask.any() or not seed_mask.any():
        return canvas_rgb.copy()

    # src must be 0 at seed pixels (region colors) so distanceTransform
    # measures, for every other pixel, distance/label to the nearest one.
    src = np.where(seed_mask, 0, 255).astype(np.uint8)
    dist, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, cv2.DIST_MASK_PRECISE, labelType=cv2.DIST_LABEL_PIXEL
    )

    gap_mask = background_mask & land_mask & (dist <= max_gap_px)
    if not gap_mask.any():
        return canvas_rgb.copy()

    # cv2 assigns each seed pixel its own label; build the label->color
    # lookup from the seeds themselves rather than assuming a label order.
    seed_ys, seed_xs = np.nonzero(seed_mask)
    seed_labels = labels[seed_ys, seed_xs]
    seed_colors = canvas_rgb[seed_ys, seed_xs]
    lut = np.zeros((int(labels.max()) + 1, 3), dtype=np.uint8)
    lut[seed_labels] = seed_colors

    filled = canvas_rgb.copy()
    filled[gap_mask] = lut[labels[gap_mask]]
    return filled
