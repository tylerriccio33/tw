"""Fills coastline gaps in an exported region_map.png raster.

Tracing borders by hand rarely lands exactly on the coastline. This
leaves small strips of background color between a region's polygon edge
and the true shore. fill_land_gaps() assigns each gap pixel the exact
color of its nearest region, with no color blending. regions.txt only
recognizes exact hex matches. An averaged color would just render as
invisible background in-game (see province_map.gd, which silently
skips unlisted colors).

Gaps on the sea side stay untouched, since water has no province to
assign.
"""

import cv2
import numpy as np


def fill_land_gaps(
    canvas_rgb: np.ndarray,
    land_mask: np.ndarray,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    """Return a copy of canvas_rgb with background pixels on land_mask
    replaced by the exact color of their nearest region pixel.
    Background pixels off land_mask (sea) stay as-is."""
    background = np.array(background_rgb, dtype=np.uint8)
    background_mask = np.all(canvas_rgb == background, axis=-1)
    gap_mask = background_mask & land_mask
    seed_mask = ~background_mask
    if not gap_mask.any() or not seed_mask.any():
        return canvas_rgb.copy()

    # src must be 0 at seed pixels (region colors) so distanceTransform
    # measures, for every other pixel, distance/label to the nearest one.
    src = np.where(seed_mask, 0, 255).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(
        src, cv2.DIST_L2, cv2.DIST_MASK_PRECISE, labelType=cv2.DIST_LABEL_PIXEL
    )

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
