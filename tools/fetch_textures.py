#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Vendor terrain PBR textures from ambientCG/Polyhaven (CC0) into assets/textures/.

Not committed to git (packed textures run ~1-2MB each, five slots). Run
`make textures` after a fresh clone. Terrain3D reads exactly two textures
per material. One is an albedo texture (RGB colour, A height). The other
is a normal texture (RGB normal, A roughness). Each source ships those as
four separate Color/NormalGL/Roughness/Displacement maps, so this script
packs them into that pair in memory. The intermediate maps never touch
disk.

world/terrain_builder.gd loads assets/textures/<slot>/{albedo,normal_rough}.png
by slot name; the TEX_* -> slot mapping lives in its PALETTE constant.
"""

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
TEXTURES_DIR = ROOT / "assets" / "textures"

# 4K rather than 1K. .gitignore excludes assets/textures/, and `make
# textures` re-vendors it, so this costs download time and VRAM, not repo
# size. At 1K a single tile had to cover so much of a 4096-unit map that the
# mip chain averaged it to flat colour well before the overview camera's
# distance.
RESOLUTION = "4K"
RESOLUTION_LOWER = "4k"

# slot name (matches terrain_builder.gd's PALETTE) -> (source, asset_id).
MATERIALS = {
    "sand": ("ambientcg", "Ground093A"),  # pale eroded coastal sand
    "tan": ("ambientcg", "Ground072"),  # dry tan plains dirt
    "grass": ("polyhaven", "aerial_grass_rock"),  # mixed grass w/ rock outcrops
    "rock": ("polyhaven", "marble_cliff_03"),  # stratified grey cliff rock
    "snow": ("ambientcg", "Snow006"),
}

# marble_cliff_03's diffuse map is a warm beige that reads almost identical to
# the tan plains texture at a distance, which makes the slope-based rock
# overlay invisible even where it's painted. Its relief/stratification is
# exactly what we want though, so grade it toward a cool grey instead of
# swapping source textures: desaturate, darken slightly, then push blue over
# red so it reads as stone rather than dirt.
COLOR_GRADE = {
    "rock": {"saturation": 0.35, "brightness": 0.82, "cool_tint": 12},
}


def _apply_color_grade(color: Image.Image, slot: str) -> Image.Image:
    grade = COLOR_GRADE.get(slot)
    if grade is None:
        return color
    graded = ImageEnhance.Color(color).enhance(grade["saturation"])
    graded = ImageEnhance.Brightness(graded).enhance(grade["brightness"])
    r, g, b = graded.split()
    tint = grade["cool_tint"]
    r = r.point(lambda v: max(0, v - tint))
    b = b.point(lambda v: min(255, v + tint))
    return Image.merge("RGB", (r, g, b))


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _download(url: str) -> bytes:
    # ambientCG (and Polyhaven's CDN, occasionally) 403 the default urllib
    # User-Agent; a browser-like one works for both.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.read()
    except Exception as exc:
        fail(f"download failed: {exc}\n  url: {url}")


def fetch_zip(asset_id: str) -> zipfile.ZipFile:
    url = f"https://ambientcg.com/get?file={asset_id}_{RESOLUTION}-JPG.zip"
    print(f"  downloading {url}")
    payload = _download(url)
    try:
        return zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        fail(f"downloaded file for {asset_id} is not a zip: {exc}")


def read_member(archive: zipfile.ZipFile, asset_id: str, suffix: str) -> Image.Image:
    name = f"{asset_id}_{RESOLUTION}-JPG_{suffix}.jpg"
    try:
        with archive.open(name) as f:
            return Image.open(io.BytesIO(f.read())).copy()
    except KeyError:
        fail(f"{name} not found in archive; contents: {archive.namelist()[:10]}")


def pack_ambientcg(asset_id: str, slot: str) -> tuple[Image.Image, Image.Image]:
    archive = fetch_zip(asset_id)
    color = _apply_color_grade(
        read_member(archive, asset_id, "Color").convert("RGB"), slot
    )
    displacement = (
        read_member(archive, asset_id, "Displacement").convert("L").resize(color.size)
    )
    albedo = Image.merge("RGBA", (*color.split(), displacement))

    normal = (
        read_member(archive, asset_id, "NormalGL").convert("RGB").resize(color.size)
    )
    roughness = (
        read_member(archive, asset_id, "Roughness").convert("L").resize(color.size)
    )
    normal_rough = Image.merge("RGBA", (*normal.split(), roughness))

    return albedo, normal_rough


def _fetch_polyhaven_map(asset_id: str, suffix: str) -> Image.Image:
    url = (
        "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/"
        f"{RESOLUTION_LOWER}/{asset_id}/{asset_id}_{suffix}_{RESOLUTION_LOWER}.jpg"
    )
    print(f"  downloading {url}")
    return Image.open(io.BytesIO(_download(url))).copy()


def pack_polyhaven(asset_id: str, slot: str) -> tuple[Image.Image, Image.Image]:
    color = _apply_color_grade(
        _fetch_polyhaven_map(asset_id, "diff").convert("RGB"), slot
    )
    displacement = (
        _fetch_polyhaven_map(asset_id, "disp").convert("L").resize(color.size)
    )
    albedo = Image.merge("RGBA", (*color.split(), displacement))

    normal = _fetch_polyhaven_map(asset_id, "nor_gl").convert("RGB").resize(color.size)
    roughness = _fetch_polyhaven_map(asset_id, "rough").convert("L").resize(color.size)
    normal_rough = Image.merge("RGBA", (*normal.split(), roughness))

    return albedo, normal_rough


PACKERS = {
    "ambientcg": pack_ambientcg,
    "polyhaven": pack_polyhaven,
}


def main() -> None:
    for slot, (source, asset_id) in MATERIALS.items():
        slot_dir = TEXTURES_DIR / slot
        albedo_path = slot_dir / "albedo.png"
        normal_path = slot_dir / "normal_rough.png"
        # Records which asset this script vendored, so raising RESOLUTION
        # actually re-fetches. A bare is_file() check cannot tell a 1K
        # download from a 4K one. That gap let `make textures` silently
        # no-op after a resolution change and left the render quietly
        # running on the previous maps.
        stamp_path = slot_dir / f".vendored-{asset_id}-{RESOLUTION}"
        if albedo_path.is_file() and normal_path.is_file() and stamp_path.is_file():
            print(f"{slot} ({asset_id}): already present at {RESOLUTION}, skipping")
            continue

        print(f"{slot} ({source}:{asset_id}) at {RESOLUTION}:")
        albedo, normal_rough = PACKERS[source](asset_id, slot)
        slot_dir.mkdir(parents=True, exist_ok=True)
        albedo.save(albedo_path)
        normal_rough.save(normal_path)
        for stale in slot_dir.glob(".vendored-*"):
            stale.unlink()
        stamp_path.touch()
        print(
            f"  wrote {albedo_path.relative_to(ROOT)}, {normal_path.relative_to(ROOT)}"
        )

    print("Textures vendored.")


if __name__ == "__main__":
    main()
