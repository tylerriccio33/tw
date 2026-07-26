#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy", "scipy"]
# ///
"""Vendor CC0 tree textures from Poly Haven into assets/foliage/.

Deliberately fetches the *textures* of Poly Haven's photoscanned trees and not
their meshes. Those meshes are 1-7 million triangles each (a fir is ~7M), and
world/scatter.gd places ~4500 trees - three orders of magnitude past what the
whole scene can afford. Decimating them is not an option either: they are built
from thousands of separate alpha-mapped twig cards, which quadric collapse
turns into confetti.

So the geometry is built in world/scatter.gd as alpha-mapped cross-cards (a
trunk plus a handful of textured quads, ~100 triangles per tree) and these
photoscanned PBR maps are what gets drawn on it. That is how real-time tree
assets are actually authored, and at campaign-map camera distance essentially
all of the visual quality lives in the texture rather than the silhouette.

Not committed to git (~2MB per map, six maps per species). Run `make foliage`
after a fresh clone. world/scatter.gd loads assets/foliage/<species>/*.png.
"""

import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
FOLIAGE_DIR = ROOT / "assets" / "foliage"

# Canopy cards are what the eye actually reads, so they get more resolution
# than the trunk, which is a handful of pixels wide in every shot preset.
CANOPY_RES = "2k"
BARK_RES = "1k"

# species -> (polyhaven asset id, canopy map prefix, bark map prefix).
#
# Poly Haven is not consistent about these prefixes across assets - a fir calls
# its foliage "twig" and its wood "bark", the island trees say "leaves" and
# "branches", the jacaranda says "leaves" and "trunk" - so each is spelled out
# rather than guessed. A wrong prefix 404s loudly at fetch time instead of
# silently vendoring a tree with no leaves.
SPECIES = {
    "conifer_fir": ("fir_tree_01", "twig", "bark"),
    "conifer_sapling": ("fir_sapling_medium", "twigs", "branches"),
    "broadleaf_island": ("island_tree_01", "leaves", "branches"),
    "broadleaf_island_02": ("island_tree_02", "leaves", "branches"),
    "broadleaf_jacaranda": ("jacaranda_tree", "leaves", "trunk"),
}

BASE = "https://dl.polyhaven.org/file/ph-assets/Models/{fmt}/{res}/{asset}/{asset}_{map}_{res}.{ext}"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def find_cells(alpha: Image.Image) -> list[dict[str, float]]:
    """Locate each individual leaf/twig in a foliage atlas, as normalised UV rects.

    These atlases are not canopy textures - they are sheets of separate leaves
    and single twigs (Poly Haven maps each one onto its own tiny card on the
    original photoscan). Stretching a whole sheet across one big quad yields
    five giant floating leaves and a tree that reads as bare sticks, which is
    exactly what the first attempt looked like.

    So the sheet is segmented here, at vendor time, and world/scatter.gd builds
    a canopy from many small cards that each sample one cell. Doing it by
    connected components rather than assuming a grid is what lets all five
    species share one code path despite having visibly different layouts.
    """
    mask = np.array(alpha) > 127
    labels, count = ndimage.label(mask)
    if count == 0:
        return []

    height, width = mask.shape
    total = float(height * width)
    cells: list[dict[str, float]] = []

    for y_slice, x_slice in ndimage.find_objects(labels):
        cell_h = y_slice.stop - y_slice.start
        cell_w = x_slice.stop - x_slice.start
        area = float(cell_h * cell_w)

        # Specks: compression noise and stray pixels, too small to be a leaf.
        if area / total < 0.0008:
            continue
        # The conifer sheets have a full-height bark strip baked down one edge.
        # It is a legitimate connected component but it is not foliage, and
        # used as a leaf card it renders as a giant brown slab.
        if cell_h > 0.9 * height or cell_w > 0.9 * width:
            continue
        # A single leaf occupying most of the sheet means segmentation failed
        # (usually everything bled together); better to drop it than to emit
        # one enormous card.
        # Generous, because a jacaranda frond is a single compound leaf that
        # legitimately covers a third of its sheet; at 0.25 they were all
        # discarded and the species came back with one usable cell.
        if area / total > 0.45:
            continue

        cells.append(
            {
                "x": x_slice.start / width,
                "y": y_slice.start / height,
                "w": cell_w / width,
                "h": cell_h / height,
            }
        )

    # Biggest first, so a card budget that cannot use them all still gets the
    # leaves that carry the most coverage.
    cells.sort(key=lambda c: c["w"] * c["h"], reverse=True)
    return cells


def _download(url: str) -> bytes:
    # Poly Haven's CDN occasionally 403s the default urllib User-Agent; a
    # browser-like one works. Same workaround as tools/fetch_textures.py.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return resp.read()
    except Exception as exc:
        fail(f"download failed: {exc}\n  url: {url}")
    raise AssertionError("unreachable")


def _fetch_map(asset: str, map_name: str, res: str) -> Image.Image:
    url = BASE.format(fmt="jpg", res=res, asset=asset, map=map_name, ext="jpg")
    print(f"  downloading {map_name}")
    return Image.open(io.BytesIO(_download(url)))


def _fetch_alpha(asset: str, map_name: str, res: str) -> Image.Image:
    # Alpha masks must come through PNG. The jpg variant exists but its lossy
    # ringing around leaf edges turns into a halo of half-transparent fringe
    # once it drives alpha scissor.
    url = BASE.format(fmt="png", res=res, asset=asset, map=map_name, ext="png")
    print(f"  downloading {map_name} (png, lossless for alpha)")
    return Image.open(io.BytesIO(_download(url)))


def build_species(species: str, asset: str, canopy: str, bark: str) -> None:
    out_dir = FOLIAGE_DIR / species
    stamp = out_dir / f".vendored-{asset}-{CANOPY_RES}-{BARK_RES}"
    if stamp.is_file():
        print(f"{species} ({asset}): already present, skipping")
        return

    print(f"{species} ({asset}):")

    # Canopy albedo carries the leaf mask in its alpha, so one texture drives
    # both colour and cutout.
    canopy_diff = _fetch_map(asset, f"{canopy}_diff", CANOPY_RES).convert("RGB")
    canopy_alpha = _fetch_alpha(asset, f"{canopy}_alpha", CANOPY_RES).convert("L")
    if canopy_alpha.size != canopy_diff.size:
        canopy_alpha = canopy_alpha.resize(canopy_diff.size, Image.LANCZOS)
    canopy_albedo = canopy_diff.copy()
    canopy_albedo.putalpha(canopy_alpha)

    canopy_normal = _fetch_map(asset, f"{canopy}_nor_gl", CANOPY_RES).convert("RGB")
    # ARM = AO in red, roughness in green, metalness in blue. Godot reads each
    # channel separately via BaseMaterial3D's *_texture_channel properties, so
    # it is used as-is rather than being split into three files.
    canopy_arm = _fetch_map(asset, f"{canopy}_arm", CANOPY_RES).convert("RGB")

    bark_diff = _fetch_map(asset, f"{bark}_diff", BARK_RES).convert("RGB")
    bark_normal = _fetch_map(asset, f"{bark}_nor_gl", BARK_RES).convert("RGB")
    bark_arm = _fetch_map(asset, f"{bark}_arm", BARK_RES).convert("RGB")

    cells = find_cells(canopy_alpha)
    if not cells:
        fail(
            f"{species}: found no usable leaf cells in {canopy}_alpha - the atlas "
            "layout may have changed, check the thresholds in find_cells()"
        )
    print(f"  segmented {len(cells)} leaf cells")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cells.json").write_text(json.dumps(cells, indent=1))
    canopy_albedo.save(out_dir / "canopy_albedo.png")
    canopy_normal.save(out_dir / "canopy_normal.png")
    canopy_arm.save(out_dir / "canopy_arm.png")
    bark_diff.save(out_dir / "bark_albedo.png")
    bark_normal.save(out_dir / "bark_normal.png")
    bark_arm.save(out_dir / "bark_arm.png")

    for stale in out_dir.glob(".vendored-*"):
        stale.unlink()
    stamp.touch()
    print(f"  wrote 6 maps to {out_dir.relative_to(ROOT)}")


def main() -> None:
    for species, (asset, canopy, bark) in SPECIES.items():
        build_species(species, asset, canopy, bark)
    print("Foliage vendored.")


if __name__ == "__main__":
    main()
