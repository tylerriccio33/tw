#!/usr/bin/env python3
"""Copy the dev map package into the live game directory.

Only ships what the game reads. That is the manifest, the faction
roster, the layer configs and rasters, the backdrop, and the derived
table and geometry.

project.json stays behind. It is the editing source, full of vertex
precision the game has no use for. Shipping it would also invite someone
to edit the live copy instead of the dev one.

This refuses to promote a package that skipped export or fails
validation. A half-written package in campaign/map_data breaks the game,
not just the tool.

Usage:
    uv run promote.py
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import export
import mapfmt

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"
GAME_DIR_DEFAULT = REPO_ROOT / "campaign" / "map_data"


def promote(dev_dir: Path, game_dir: Path) -> list[str]:
    package = mapfmt.load_package(dev_dir)
    project = mapfmt.load_project(dev_dir, package)

    problems = export.validate_package(project, package)
    if problems:
        raise SystemExit(
            "Refusing to promote, the dev package doesn't validate:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    for required in (mapfmt.TABLE_NAME, mapfmt.GEO_NAME):
        if not (dev_dir / required).is_file():
            raise SystemExit(
                f"{dev_dir / required} is missing - export from the editor first"
            )

    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / mapfmt.LAYERS_DIRNAME).mkdir(exist_ok=True)

    copied = []
    for name in (
        mapfmt.MANIFEST_NAME,
        mapfmt.TABLE_NAME,
        mapfmt.GEO_NAME,
        package.manifest.get("factions", "factions.json"),
        package.manifest.get("backdrop", "backdrop.png"),
    ):
        source = dev_dir / name
        if source.is_file():
            shutil.copy2(source, game_dir / name)
            copied.append(name)

    for layer_name in package.layer_order:
        cfg = package.layers[layer_name]
        for rel in (f"{layer_name}.json", cfg.raster):
            source = dev_dir / mapfmt.LAYERS_DIRNAME / rel
            if source.is_file():
                shutil.copy2(source, game_dir / mapfmt.LAYERS_DIRNAME / rel)
                copied.append(f"{mapfmt.LAYERS_DIRNAME}/{rel}")

    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-dir", default=str(DEV_DIR_DEFAULT))
    parser.add_argument("--game-dir", default=str(GAME_DIR_DEFAULT))
    args = parser.parse_args()

    copied = promote(Path(args.dev_dir), Path(args.game_dir))
    print(f"copied {len(copied)} files -> {args.game_dir}")


if __name__ == "__main__":
    main()
