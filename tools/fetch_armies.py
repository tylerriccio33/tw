#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Vendor army art into assets/armies/: Quaternius's CC0 knight model plus the
2D chess-knight marker icon used by campaign/army_marker.gd.

Unlike tools/fetch_buildings.py, OpenGameArt mirrors the knight model behind a
stable, unauthenticated URL. It skips itch.io's session-scoped download
token, so the whole fetch runs unattended in one step.

CC0 licensed - https://creativecommons.org/publicdomain/zero/1.0/, no
attribution required. tools/vendor_cache/ and assets/armies/ are both
gitignored; run `make armies` after a fresh clone.

The marker icon is CC BY 3.0 from game-icons.net (see
assets/armies/icons/LICENSE.txt for attribution). Rasterizing it needs
`rsvg-convert` (`brew install librsvg`) on PATH.
"""

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_ZIP = ROOT / "tools" / "vendor_cache" / "knight_character_quaternius.zip"
OUT_DIR = ROOT / "assets" / "armies" / "kit"
STAMP = ROOT / "assets" / "armies" / ".vendored-knight-character-quaternius"

DOWNLOAD_URL = "https://opengameart.org/sites/default/files/Knight%20Character%20by%20%40Quaternius.zip"

# Just the rigged body. The pack parents separate helmet/weapon accessory
# meshes to bones for outfit variety we don't use yet.
MEMBER = "Knight Character by @Quaternius/FBX/KnightCharacter.fbx"

ICON_DIR = ROOT / "assets" / "armies" / "icons"
ICON_STAMP = ICON_DIR / ".vendored-chess-knight-game-icons"
ICON_SVG_URL = (
    "https://raw.githubusercontent.com/game-icons/icons/master/skoll/chess-knight.svg"
)


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def vendor_knight_model() -> None:
    if STAMP.is_file():
        print("armies: knight model already vendored, skipping")
        return

    if not CACHE_ZIP.is_file():
        print(f"downloading {DOWNLOAD_URL}")
        CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(DOWNLOAD_URL, CACHE_ZIP)
        except OSError as exc:
            fail(f"download failed: {exc}")

    print(f"unpacking {CACHE_ZIP.name}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(CACHE_ZIP) as zf:
        if MEMBER not in zf.namelist():
            fail(
                f"{MEMBER!r} not found in {CACHE_ZIP.name} - pack layout may have changed"
            )
        with zf.open(MEMBER) as src, open(OUT_DIR / "KnightCharacter.fbx", "wb") as dst:
            dst.write(src.read())

    print(f"wrote KnightCharacter.fbx to {OUT_DIR.relative_to(ROOT)}")
    STAMP.touch()
    print("Knight model vendored.")


def vendor_marker_icon() -> None:
    if ICON_STAMP.is_file():
        print("armies: marker icon already vendored, skipping")
        return

    rsvg_convert = shutil.which("rsvg-convert")
    if rsvg_convert is None:
        fail("rsvg-convert not found on PATH - install it with `brew install librsvg`")

    print(f"downloading {ICON_SVG_URL}")
    try:
        svg_bytes = urllib.request.urlopen(ICON_SVG_URL).read()
    except OSError as exc:
        fail(f"download failed: {exc}")

    # Drop the icon's opaque background square, leaving only the white
    # silhouette so army_marker.gd can tint it per faction at runtime.
    svg_text = svg_bytes.decode("utf-8").replace('<path d="M0 0h512v512H0z"/>', "")

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    svg_path = ICON_DIR / "chess_knight.svg"
    svg_path.write_text(svg_text)

    png_path = ICON_DIR / "chess_knight.png"
    # Rasterized close to the marker's on-screen size (see DISC_RADIUS in
    # army_marker.gd) rather than at a large master resolution: Godot doesn't
    # generate mipmaps for this import by default, so a much bigger source
    # texture minified down at draw time produces visible moire/aliasing on
    # this icon's fine linework.
    subprocess.run(
        [rsvg_convert, "-w", "128", "-h", "128", str(svg_path), "-o", str(png_path)],
        check=True,
    )
    svg_path.unlink()

    (ICON_DIR / "LICENSE.txt").write_text(
        "chess_knight.png\n"
        "Source: https://game-icons.net/1x1/skoll/chess-knight.html\n"
        "Author: Skoll (https://game-icons.net)\n"
        "License: CC BY 3.0 (https://creativecommons.org/licenses/by/3.0/)\n"
        "Modified: background square removed, leaving only the white silhouette so it\n"
        "can be tinted per-faction at runtime; rasterized from SVG to a 256x256 PNG.\n"
    )

    print(f"wrote chess_knight.png to {ICON_DIR.relative_to(ROOT)}")
    ICON_STAMP.touch()
    print("Marker icon vendored.")


def main() -> None:
    vendor_knight_model()
    vendor_marker_icon()


if __name__ == "__main__":
    main()
