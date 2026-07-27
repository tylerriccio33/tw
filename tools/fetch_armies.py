#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Vendor Quaternius's CC0 LowPoly Animated Knight into assets/armies/.

Unlike tools/fetch_buildings.py, this asset is mirrored on OpenGameArt behind
a stable, unauthenticated URL rather than itch.io's session-scoped download
token, so the whole fetch can run unattended in one step.

campaign/army_models.gd loads assets/armies/kit/KnightCharacter.fbx and tints
it per faction to draw standing armies on the campaign map.

CC0 licensed - https://creativecommons.org/publicdomain/zero/1.0/, no
attribution required. tools/vendor_cache/ and assets/armies/ are both
gitignored; run `make armies` after a fresh clone.
"""

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_ZIP = ROOT / "tools" / "vendor_cache" / "knight_character_quaternius.zip"
OUT_DIR = ROOT / "assets" / "armies" / "kit"
STAMP = ROOT / "assets" / "armies" / ".vendored-knight-character-quaternius"

DOWNLOAD_URL = "https://opengameart.org/sites/default/files/Knight%20Character%20by%20%40Quaternius.zip"

# Just the rigged body - the separate helmet/weapon accessory meshes in the
# pack are parented to bones for outfit variety we don't use yet.
MEMBER = "Knight Character by @Quaternius/FBX/KnightCharacter.fbx"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if STAMP.is_file():
        print("armies: already vendored, skipping")
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
    print("Armies vendored.")


if __name__ == "__main__":
    main()
