#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Vendor GUT (Gut Unit Test), the GDScript unit-testing addon, into addons/gut/.

GUT isn't part of this repo's source. It's a third-party Godot addon,
fetched the same way tools/fetch_armies.py vendors the knight model.
.gitignore excludes addons/gut/; run `make gut` after a fresh clone.

https://github.com/bitwes/Gut - MIT licensed.
"""

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "9.6.1"
CACHE_ZIP = ROOT / "tools" / "vendor_cache" / f"gut-{VERSION}.zip"
OUT_DIR = ROOT / "addons" / "gut"
STAMP = OUT_DIR / ".vendored-version"

DOWNLOAD_URL = f"https://github.com/bitwes/Gut/archive/refs/tags/v{VERSION}.zip"
MEMBER_PREFIX = f"Gut-{VERSION}/addons/gut/"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if STAMP.is_file() and STAMP.read_text().strip() == VERSION:
        print(f"gut: already vendored at {VERSION}, skipping")
        return

    if not CACHE_ZIP.is_file():
        print(f"downloading {DOWNLOAD_URL}")
        CACHE_ZIP.parent.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(DOWNLOAD_URL, CACHE_ZIP)
        except OSError as exc:
            fail(f"download failed: {exc}")

    print(f"unpacking {CACHE_ZIP.name}")
    if OUT_DIR.is_dir():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    with zipfile.ZipFile(CACHE_ZIP) as zf:
        members = [
            n
            for n in zf.namelist()
            if n.startswith(MEMBER_PREFIX) and not n.endswith("/")
        ]
        if not members:
            fail(
                f"no {MEMBER_PREFIX!r} entries in {CACHE_ZIP.name} - release layout may have changed"
            )
        for member in members:
            rel = member[len(MEMBER_PREFIX) :]
            dest = OUT_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)

    STAMP.write_text(VERSION)
    print(f"gut: vendored {VERSION} -> {OUT_DIR}")


if __name__ == "__main__":
    main()
