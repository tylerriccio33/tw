#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Vendor Quaternius's CC0 Medieval Village MegaKit into assets/buildings/.

Unlike tools/fetch_textures.py and tools/fetch_foliage.py, this cannot hit a
stable CDN URL unattended: itch.io serves the pack through a
pay-what-you-want click-through page that mints a session-scoped, expiring
download token, not a fixed link. So the fetch step is split in two:

  1. A human (or an agent driving a real browser) downloads the free
     "Standard" zip from https://quaternius.itch.io/medieval-village-megakit
     ("No thanks, just take me to the downloads") and saves it to
     tools/vendor_cache/medieval_village_megakit_standard.zip.
  2. This script unpacks that cached zip's glTF/ directory (meshes, .bin
     buffers and the shared texture atlas) into assets/buildings/kit/.

world/cities.gd loads individual pieces from assets/buildings/kit/ by name
(see BUILDING_RECIPES) and assembles them into houses/keeps at build time.

CC0 licensed - https://creativecommons.org/publicdomain/zero/1.0/, no
attribution required. tools/vendor_cache/ and assets/buildings/ are both
gitignored; run `make buildings` after a fresh clone.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_ZIP = ROOT / "tools" / "vendor_cache" / "medieval_village_megakit_standard.zip"
OUT_DIR = ROOT / "assets" / "buildings" / "kit"
STAMP = ROOT / "assets" / "buildings" / ".vendored-medieval-village-megakit-standard"

DOWNLOAD_URL = "https://quaternius.itch.io/medieval-village-megakit"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if STAMP.is_file():
        print("buildings: already vendored, skipping")
        return

    if not CACHE_ZIP.is_file():
        fail(
            "no cached download at "
            f"{CACHE_ZIP.relative_to(ROOT)}\n\n"
            f"  Download the free Standard pack from {DOWNLOAD_URL}\n"
            '  ("No thanks, just take me to the downloads" - no payment or '
            "login needed)\n"
            f"  and save the zip to {CACHE_ZIP.relative_to(ROOT)}, then rerun "
            "`make buildings`."
        )

    print(f"unpacking {CACHE_ZIP.name}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(CACHE_ZIP) as zf:
        members = [
            m
            for m in zf.namelist()
            if "/glTF/" in m and not m.endswith("/") and "__MACOSX" not in m
        ]
        if not members:
            fail(
                f"no glTF/ directory found inside {CACHE_ZIP.name} - pack layout may have changed"
            )
        for member in members:
            name = Path(member).name
            with zf.open(member) as src, open(OUT_DIR / name, "wb") as dst:
                dst.write(src.read())

    count = len(list(OUT_DIR.glob("*.gltf")))
    print(
        f"wrote {count} pieces (+ .bin buffers and textures) to {OUT_DIR.relative_to(ROOT)}"
    )
    STAMP.touch()
    print("Buildings vendored.")


if __name__ == "__main__":
    main()
