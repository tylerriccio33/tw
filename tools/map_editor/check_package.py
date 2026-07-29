#!/usr/bin/env python3
"""Validate a map package without writing anything.

Same checks the editor's Export button runs, minus the export. It exits
non-zero on the first problem, so CI can gate on a coherent map. The
alternative is the game finding out, on a province with no land.

Usage:
    uv run check_package.py
    uv run check_package.py --package-dir ../../campaign/map_data
"""

from __future__ import annotations

import argparse
from pathlib import Path

import export
import mapfmt

DEV_DIR_DEFAULT = Path(__file__).resolve().parent / "dev_map_data"


def check(package_dir: Path) -> list[str]:
    package = mapfmt.load_package(package_dir)
    project = mapfmt.load_project(package_dir, package)
    return export.validate_package(project, package)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", default=str(DEV_DIR_DEFAULT))
    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    try:
        problems = check(package_dir)
    except mapfmt.PackageError as exc:
        raise SystemExit(f"{package_dir} isn't a readable map package:\n  {exc}")

    if problems:
        print(f"{len(problems)} problem(s) in {package_dir}:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)

    package = mapfmt.load_package(package_dir)
    print(f"{package_dir} is valid")
    print(f"  size:   {package.size[0]}x{package.size[1]}")
    print(f"  layers: {', '.join(package.layer_order)}")
    table_path = package_dir / mapfmt.TABLE_NAME
    if table_path.is_file():
        print(f"  exported provinces: {len(mapfmt.read_province_table(package_dir))}")
    else:
        print("  not exported yet (no provinces.table.json)")


if __name__ == "__main__":
    main()
