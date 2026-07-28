"""Reconciles an exported region_map.png raster against the land/sea
classification in server.build_land_mask, in both directions.

fill_land_gaps() closes small background strips left on land, from a
polygon edge not quite reaching the true shore. clip_sea_overflow()
reverts region color that spilled onto water, from a polygon traced
too loosely over the shoreline. Both keep colors exact, never blended.
regions.txt only recognizes exact hex matches. A blended color would
just render as invisible background in-game (see province_map.gd,
which silently skips unlisted colors).

fill_land_gaps() only bridges gaps up to MAX_GAP_PX. An untraced
country can be an arbitrarily large patch of background. Flooding a
neighboring region's color across all of it would silently annex land
nobody drew.
"""

import cv2
import numpy as np

MAX_GAP_PX = 8  # widest untraced strip a gap-fill will bridge
MIN_OVERFLOW_AREA_PX = 3000  # smallest contiguous sea-overflow blob to clip


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


def clip_sea_overflow(
    canvas_rgb: np.ndarray,
    land_mask: np.ndarray,
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    min_area_px: int = MIN_OVERFLOW_AREA_PX,
) -> np.ndarray:
    """Return a copy of canvas_rgb with region-colored pixels that fall
    off land_mask (i.e. classified as sea) reverted to background_rgb.
    Unlike fill_land_gaps, this has no distance cap. A polygon traced
    over open water, however far, gets clipped back to the shoreline.

    Only contiguous overflow blobs of at least min_area_px get clipped.
    land_mask is a coarse heuristic, not per-pixel-exact. Scattered
    single-pixel "sea" misclassifications inside a region (river
    glare, shadow, texture noise) stay put instead of punching
    holes."""
    background = np.array(background_rgb, dtype=np.uint8)
    region_mask = ~np.all(canvas_rgb == background, axis=-1)
    overflow_mask = region_mask & ~land_mask
    if not overflow_mask.any():
        return canvas_rgb.copy()

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        overflow_mask.astype(np.uint8), connectivity=8
    )
    large_overflow = np.zeros_like(overflow_mask)
    for label in range(1, num_labels):
        if stats[label, cv2.CC_STAT_AREA] >= min_area_px:
            large_overflow |= labels == label
    if not large_overflow.any():
        return canvas_rgb.copy()

    clipped = canvas_rgb.copy()
    clipped[large_overflow] = background
    return clipped
