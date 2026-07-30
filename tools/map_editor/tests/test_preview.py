"""preview.py: composite the layer stack into one reviewable PNG.

Runs the same validate_package the Export button does, and outlines
any flagged polygon in red. Tests check both the happy path - a clean,
backdrop-sized composite - and that a problem polygon gets flagged.
"""

import mapfmt
import preview
import pytest
from PIL import Image
from tests.conftest import box, project_with


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def test_render_preview_is_backdrop_sized_with_no_problems_for_a_clean_package(package):
    project = project_with(package, provinces=two_provinces())

    image, problems = preview.render_preview(project, package)

    assert image.size == package.size
    assert problems == []


def test_render_preview_flags_a_self_intersecting_polygon(package):
    bowtie = [[10, 8], [30, 32], [30, 8], [10, 32]]
    project = project_with(
        package,
        provinces=[{"id": 1, "key": "west", "name": "West", "polygons": [bowtie]}],
    )

    image, problems = preview.render_preview(project, package)

    assert problems
    assert image.size == package.size


def test_render_preview_skips_layers_never_exported_to_a_raster(package):
    """Only coastline/provinces have geometry drawn in project_with. The
    brush layers (terrain, resources, ownership) have no raster on disk
    yet in a fresh package. render_preview must not crash on that."""
    project = project_with(package, provinces=two_provinces())
    for layer in ("terrain", "resources"):
        raster = package.raster_path(layer)
        if raster.is_file():
            raster.unlink()

    image, _problems = preview.render_preview(project, package)
    assert image.size == package.size


def test_main_writes_a_png_and_exits_zero_for_a_clean_package(package, capsys):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    import sys

    argv = sys.argv
    out_path = package.root / "preview.png"
    sys.argv = [
        "preview.py",
        "--package-dir",
        str(package.root),
        "--out",
        str(out_path),
    ]
    try:
        preview.main()
    finally:
        sys.argv = argv

    assert out_path.is_file()
    with Image.open(out_path) as im:
        assert im.size == package.size
    out = capsys.readouterr().out
    assert "no validation problems" in out


def test_main_exits_nonzero_and_lists_problems_for_a_broken_package(package, capsys):
    bowtie = [[10, 8], [30, 32], [30, 8], [10, 32]]
    project = project_with(
        package,
        provinces=[{"id": 1, "key": "west", "name": "West", "polygons": [bowtie]}],
    )
    mapfmt.save_project(package.root, project)

    import sys

    argv = sys.argv
    out_path = package.root / "preview.png"
    sys.argv = [
        "preview.py",
        "--package-dir",
        str(package.root),
        "--out",
        str(out_path),
    ]
    try:
        with pytest.raises(SystemExit) as exc_info:
            preview.main()
    finally:
        sys.argv = argv

    assert exc_info.value.code == 1
    assert out_path.is_file()
    out = capsys.readouterr().out
    assert "problem(s)" in out


def test_main_defaults_out_path_to_package_dir_preview_png(package, capsys):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    import sys

    argv = sys.argv
    sys.argv = ["preview.py", "--package-dir", str(package.root)]
    try:
        preview.main()
    finally:
        sys.argv = argv

    assert (package.root / "preview.png").is_file()
