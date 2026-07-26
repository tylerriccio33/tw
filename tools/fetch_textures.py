#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Vendor terrain PBR textures from ambientCG (CC0) into assets/textures/.

Not committed to git (packed textures run ~1-2MB each, five slots). Run
`make textures` after a fresh clone. Terrain3D reads exactly two textures per
material - an albedo texture (RGB colour, A height) and a normal texture
(RGB normal, A roughness) - so each ambientCG source (which ships those as
four separate Color/NormalGL/Roughness/Displacement maps) gets packed into
that pair in memory; the intermediate maps are never written to disk.

world/terrain_builder.gd loads assets/textures/<slot>/{albedo,normal_rough}.png
by slot name; the TEX_* -> slot mapping lives in its PALETTE constant.
"""

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TEXTURES_DIR = ROOT / "assets" / "textures"

RESOLUTION = "1K"

# slot name (matches terrain_builder.gd's PALETTE) -> ambientCG asset id.
MATERIALS = {
    "sand": "Ground093A",  # pale eroded coastal sand
    "tan": "Ground072",  # dry tan plains dirt
    "grass": "Grass001",
    "rock": "Rock051",  # grey rock with lichen
    "snow": "Snow006",
}


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def fetch_zip(asset_id: str) -> zipfile.ZipFile:
    url = f"https://ambientcg.com/get?file={asset_id}_{RESOLUTION}-JPG.zip"
    print(f"  downloading {url}")
    # ambientCG 403s the default urllib User-Agent; a browser-like one works.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            payload = resp.read()
    except Exception as exc:
        fail(f"download failed for {asset_id}: {exc}\n  url: {url}")
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


def pack(asset_id: str) -> tuple[Image.Image, Image.Image]:
    archive = fetch_zip(asset_id)
    color = read_member(archive, asset_id, "Color").convert("RGB")
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


def main() -> None:
    for slot, asset_id in MATERIALS.items():
        slot_dir = TEXTURES_DIR / slot
        albedo_path = slot_dir / "albedo.png"
        normal_path = slot_dir / "normal_rough.png"
        if albedo_path.is_file() and normal_path.is_file():
            print(f"{slot} ({asset_id}): already present, skipping")
            continue

        print(f"{slot} ({asset_id}):")
        albedo, normal_rough = pack(asset_id)
        slot_dir.mkdir(parents=True, exist_ok=True)
        albedo.save(albedo_path)
        normal_rough.save(normal_path)
        print(
            f"  wrote {albedo_path.relative_to(ROOT)}, {normal_path.relative_to(ROOT)}"
        )

    print("Textures vendored.")


if __name__ == "__main__":
    main()
