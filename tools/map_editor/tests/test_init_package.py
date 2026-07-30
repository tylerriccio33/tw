"""init_package.py: bootstrapping a fresh package from a backdrop.

Uses the small synthetic backdrop from conftest, not the real game art -
test_realistic.py already covers the full-scale case. These run even in
a checkout with no campaign art.
"""

import init_package
import mapfmt
import numpy as np
import pytest
from tests.conftest import write_backdrop


def test_seed_provinces_with_zero_count_returns_nothing():
    land_mask = np.ones((20, 20), dtype=bool)
    features, points = init_package.seed_provinces(land_mask, 0)
    assert features == []
    assert points == {}


def test_seed_provinces_on_an_empty_land_mask_returns_nothing():
    land_mask = np.zeros((20, 20), dtype=bool)
    features, points = init_package.seed_provinces(land_mask, 5)
    assert features == []
    assert points == {}


def test_trace_land_features_on_a_blank_mask_returns_nothing():
    assert init_package.trace_land_features(np.zeros((20, 20), dtype=np.uint8)) == []


def test_init_package_refuses_to_overwrite_an_existing_manifest_without_force(
    tmp_path,
):
    backdrop = tmp_path / "backdrop.png"
    write_backdrop(backdrop)
    root = tmp_path / "package"

    init_package.init_package(root, backdrop)
    with pytest.raises(SystemExit, match="already exists"):
        init_package.init_package(root, backdrop)


def test_init_package_overwrites_configs_when_forced(tmp_path):
    backdrop = tmp_path / "backdrop.png"
    write_backdrop(backdrop)
    root = tmp_path / "package"

    init_package.init_package(root, backdrop)
    summary = init_package.init_package(root, backdrop, force=True)
    assert summary["size"] == [60, 40]


def test_init_package_seeds_placeholder_provinces_when_asked(tmp_path):
    backdrop = tmp_path / "backdrop.png"
    write_backdrop(backdrop)
    root = tmp_path / "package"

    summary = init_package.init_package(root, backdrop, province_count=3)

    assert summary["provinces"] > 0
    package = mapfmt.load_package(root)
    project = mapfmt.load_project(root, package)
    assert len(project["layers"]["provinces"]["features"]) == summary["provinces"]


def test_main_runs_end_to_end_via_cli_args(tmp_path, capsys):
    backdrop = tmp_path / "backdrop.png"
    write_backdrop(backdrop)
    root = tmp_path / "package"

    import sys

    argv = sys.argv
    sys.argv = [
        "init_package.py",
        "--package-dir",
        str(root),
        "--backdrop",
        str(backdrop),
        "--seed-provinces",
        "2",
    ]
    try:
        init_package.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "package at" in out
    assert "provinces:" in out
    assert (root / mapfmt.MANIFEST_NAME).is_file()
