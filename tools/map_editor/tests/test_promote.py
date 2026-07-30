"""promote.py: copy a validated dev package into the game directory.

Never touches campaign/map_data - every test points --dev-dir/--game-dir
at tmp_path so a bug here can't clobber the live map.
"""

import mapfmt
import promote
import pytest
from tests.conftest import box, project_with


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def exported_package(package):
    import export

    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)
    export.export_package(project, package)
    return package


def bowtie_province():
    return [
        {
            "id": 1,
            "key": "west",
            "name": "West",
            "polygons": [[[10, 8], [30, 32], [30, 8], [10, 32]]],
        }
    ]


def test_promote_refuses_a_package_that_fails_validation(package):
    project = project_with(package, provinces=bowtie_province())
    mapfmt.save_project(package.root, project)

    with pytest.raises(SystemExit, match="doesn't validate"):
        promote.promote(package.root, package.root / "game")


def test_promote_refuses_a_valid_package_that_was_never_exported(package):
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    with pytest.raises(SystemExit, match="export from the editor first"):
        promote.promote(package.root, package.root / "game")


def test_promote_copies_manifest_table_geo_and_layer_files(package):
    exported_package(package)
    game_dir = package.root.parent / "game"

    copied = promote.promote(package.root, game_dir)

    assert mapfmt.MANIFEST_NAME in copied
    assert mapfmt.TABLE_NAME in copied
    assert mapfmt.GEO_NAME in copied
    assert (game_dir / mapfmt.MANIFEST_NAME).is_file()
    assert (game_dir / mapfmt.TABLE_NAME).is_file()
    assert (game_dir / mapfmt.GEO_NAME).is_file()
    assert (game_dir / "factions.json").is_file()
    assert (game_dir / "backdrop.png").is_file()
    for layer_name in package.layer_order:
        cfg = package.layers[layer_name]
        assert (game_dir / mapfmt.LAYERS_DIRNAME / cfg.raster).is_file()
        assert (game_dir / mapfmt.LAYERS_DIRNAME / f"{layer_name}.json").is_file()


def test_promote_does_not_copy_project_json(package):
    exported_package(package)
    game_dir = package.root.parent / "game"

    promote.promote(package.root, game_dir)

    assert not (game_dir / mapfmt.PROJECT_NAME).exists()


def test_promote_skips_layer_rasters_that_were_never_painted(package):
    """A brush layer nobody drew on has no raster on disk. Promote should
    skip it rather than crash trying to copy a file that isn't there."""
    exported_package(package)
    # terrain is a brush layer; export writes a raster for it even if
    # empty, so instead check a layer config with no matching raster is
    # tolerated by deleting one after export.
    terrain_raster = (
        package.root / mapfmt.LAYERS_DIRNAME / package.layers["terrain"].raster
    )
    terrain_raster.unlink()
    game_dir = package.root.parent / "game"

    copied = promote.promote(package.root, game_dir)

    assert f"{mapfmt.LAYERS_DIRNAME}/{package.layers['terrain'].raster}" not in copied
    assert not (
        game_dir / mapfmt.LAYERS_DIRNAME / package.layers["terrain"].raster
    ).exists()


def test_main_promotes_using_cli_args(package, capsys):
    exported_package(package)
    game_dir = package.root.parent / "game"

    import sys

    argv = sys.argv
    sys.argv = [
        "promote.py",
        "--dev-dir",
        str(package.root),
        "--game-dir",
        str(game_dir),
    ]
    try:
        promote.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "copied" in out
    assert (game_dir / mapfmt.TABLE_NAME).is_file()
