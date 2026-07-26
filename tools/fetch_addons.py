#!/usr/bin/env python3
"""Vendor the Terrain3D GDExtension into addons/.

Not committed to git (42MB of prebuilt binaries). Run `make addons` after a
fresh clone. Fails loudly rather than leaving a half-populated addons/ dir,
because a partial GDExtension surfaces later as an inscrutable Godot crash.
"""

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

TERRAIN3D_VERSION = "v1.0.2-stable"
TERRAIN3D_URL = (
    "https://github.com/TokisanGames/Terrain3D/releases/download/"
    f"{TERRAIN3D_VERSION}/Terrain3D_{TERRAIN3D_VERSION}.zip"
)

ROOT = Path(__file__).resolve().parent.parent
ADDONS = ROOT / "addons"
TARGET = ADDONS / "terrain_3d"


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if TARGET.exists():
        print(
            f"addons/terrain_3d already present ({TERRAIN3D_VERSION} expected); "
            f"delete it to re-fetch"
        )
        return

    print(f"downloading {TERRAIN3D_URL}")
    try:
        with urllib.request.urlopen(TERRAIN3D_URL) as resp:
            payload = resp.read()
    except Exception as exc:
        fail(f"download failed: {exc}\n  url: {TERRAIN3D_URL}")

    print(f"  got {len(payload) / 1e6:.1f} MB")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        fail(f"downloaded file is not a zip: {exc}")

    # The release zip nests everything under addons/terrain_3d/. Find that
    # prefix rather than assuming it, so a repackaged release fails visibly.
    members = archive.namelist()
    prefix = None
    for name in members:
        if name.endswith("terrain_3d/terrain.gdextension"):
            prefix = name[: -len("terrain.gdextension")]
            break
    if prefix is None:
        fail(
            "could not locate terrain.gdextension in the release zip; "
            f"archive layout changed. Top entries: {members[:10]}"
        )

    ADDONS.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for name in members:
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix) :]
        dest = TARGET / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(name) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
        extracted += 1

    if extracted == 0:
        fail(f"extracted 0 files from prefix {prefix!r}")
    print(f"  extracted {extracted} files -> {TARGET.relative_to(ROOT)}")

    if not (TARGET / "terrain.gdextension").is_file():
        fail("terrain.gdextension missing after extraction")

    macos_lib = (
        TARGET
        / "bin"
        / "libterrain.macos.release.framework"
        / "libterrain.macos.release"
    )
    if sys.platform == "darwin" and not macos_lib.is_file():
        fail(f"macOS release binary missing: {macos_lib.relative_to(ROOT)}")

    # macOS quarantines unsigned dylibs from downloaded archives; Godot then
    # fails to load the GDExtension with a misleading "not found" style error.
    if sys.platform == "darwin":
        subprocess.run(
            ["xattr", "-dr", "com.apple.quarantine", str(TARGET)], check=True
        )
        print("  cleared macOS quarantine attribute")

    print(f"Terrain3D {TERRAIN3D_VERSION} vendored.")


if __name__ == "__main__":
    main()
