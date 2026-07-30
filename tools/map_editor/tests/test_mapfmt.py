"""mapfmt: the package format itself - colors, layer configs, and the
package/project loaders.

Mostly guard rails. A malformed manifest or layer config should fail
loudly with a PackageError that names the problem. It should not crash
somewhere downstream with a bare KeyError.
"""

import json

import mapfmt
import pytest

# ---------------------------------------------------------------------------
# colors
# ---------------------------------------------------------------------------


def test_hex_to_rgb_rejects_a_short_string():
    with pytest.raises(mapfmt.PackageError, match="not a #rrggbb color"):
        mapfmt.hex_to_rgb("#fff")


def test_hex_to_rgb_rejects_non_hex_digits():
    with pytest.raises(mapfmt.PackageError, match="not a #rrggbb color"):
        mapfmt.hex_to_rgb("#zzzzzz")


def test_hex_to_rgb_round_trips_with_rgb_to_hex():
    assert mapfmt.rgb_to_hex(mapfmt.hex_to_rgb("#7d9a4e")) == "#7d9a4e"


def test_id_to_color_rejects_zero_and_out_of_range():
    with pytest.raises(mapfmt.PackageError, match="out of range"):
        mapfmt.id_to_color(0)
    with pytest.raises(mapfmt.PackageError, match="out of range"):
        mapfmt.id_to_color(0x1000000)


# ---------------------------------------------------------------------------
# layer config parsing
# ---------------------------------------------------------------------------


def valid_layer_dict(**overrides):
    base = {
        "name": "terrain",
        "input": "brush",
        "kind": "mask",
        "raster": "terrain.png",
        "legend": {"#7d9a4e": {"key": "plains", "name": "Plains"}},
        "nodata_color": "#000000",
    }
    base.update(overrides)
    return base


def test_parse_layer_config_requires_a_name():
    with pytest.raises(mapfmt.PackageError, match="no 'name'"):
        mapfmt.parse_layer_config(valid_layer_dict(name=""))


def test_parse_layer_config_rejects_an_unknown_input():
    with pytest.raises(mapfmt.PackageError, match="expected one of"):
        mapfmt.parse_layer_config(valid_layer_dict(input="scribble"))


def test_parse_layer_config_rejects_an_unknown_kind():
    with pytest.raises(mapfmt.PackageError, match="expected one of"):
        mapfmt.parse_layer_config(valid_layer_dict(kind="nonsense"))


def test_parse_layer_config_rejects_a_legend_entry_missing_a_key():
    bad = valid_layer_dict(legend={"#7d9a4e": {"name": "Plains"}})
    with pytest.raises(mapfmt.PackageError, match="has no 'key'"):
        mapfmt.parse_layer_config(bad)


def test_parse_layer_config_rejects_an_identity_layer_with_a_legend():
    bad = valid_layer_dict(kind="identity", legend={"#7d9a4e": {"key": "plains"}})
    with pytest.raises(mapfmt.PackageError, match="must not also declare a legend"):
        mapfmt.parse_layer_config(bad)


def test_parse_layer_config_rejects_a_non_identity_layer_with_no_legend():
    bad = valid_layer_dict(legend={})
    with pytest.raises(mapfmt.PackageError, match="declares no legend"):
        mapfmt.parse_layer_config(bad)


def test_parse_layer_config_rejects_an_unknown_reduce_mode():
    bad = valid_layer_dict(reduce={"mode": "nonsense", "into": "summary"})
    with pytest.raises(mapfmt.PackageError, match="reduce.mode"):
        mapfmt.parse_layer_config(bad)


def test_parse_layer_config_rejects_a_reduce_with_no_into_tag():
    bad = valid_layer_dict(reduce={"mode": "majority"})
    with pytest.raises(mapfmt.PackageError, match="no 'into' tag name"):
        mapfmt.parse_layer_config(bad)


def test_color_for_key_and_entry_for_key_raise_for_an_unknown_key(package):
    cfg = package.layers["terrain"]
    with pytest.raises(mapfmt.PackageError, match="no legend entry for key"):
        cfg.color_for_key("not-a-real-key")
    with pytest.raises(mapfmt.PackageError, match="no legend entry for key"):
        cfg.entry_for_key("not-a-real-key")


def test_mask_refs_lists_every_legend_key_namespaced_by_layer(package):
    cfg = package.layers["terrain"]
    refs = mapfmt.mask_refs(cfg)
    assert all(r.startswith("terrain:") for r in refs)
    assert len(refs) == len(cfg.legend)


# ---------------------------------------------------------------------------
# package loading
# ---------------------------------------------------------------------------


def test_load_package_rejects_a_directory_with_no_manifest(tmp_path):
    with pytest.raises(mapfmt.PackageError, match="not a map package"):
        mapfmt.load_package(tmp_path)


def test_load_package_rejects_a_manifest_missing_a_required_field(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    (root / mapfmt.MANIFEST_NAME).write_text(json.dumps({"size": [10, 10]}))
    with pytest.raises(mapfmt.PackageError, match="has no 'layers'"):
        mapfmt.load_package(root)


def test_load_package_rejects_a_manifest_listing_an_undeclared_layer(tmp_path):
    root = tmp_path / "package"
    (root / mapfmt.LAYERS_DIRNAME).mkdir(parents=True)
    man = {"size": [10, 10], "layers": ["ghost"], "province_layer": "ghost"}
    (root / mapfmt.MANIFEST_NAME).write_text(json.dumps(man))
    with pytest.raises(mapfmt.PackageError, match="doesn't exist"):
        mapfmt.load_package(root)


def test_load_package_rejects_a_province_layer_not_in_the_layer_list(package, tmp_path):
    man = dict(package.manifest)
    man["province_layer"] = "nonexistent"
    root = tmp_path / "broken"
    _copy_package(package, root, manifest=man)

    with pytest.raises(mapfmt.PackageError, match="isn't one of the declared layers"):
        mapfmt.load_package(root)


def test_load_package_rejects_a_non_identity_province_layer(package, tmp_path):
    man = dict(package.manifest)
    man["province_layer"] = "terrain"  # a mask layer, not identity
    root = tmp_path / "broken"
    _copy_package(package, root, manifest=man)

    with pytest.raises(mapfmt.PackageError, match="must be kind=identity"):
        mapfmt.load_package(root)


def test_load_package_rejects_an_unknown_city_layer(package, tmp_path):
    man = dict(package.manifest)
    man["city_layer"] = "ghost"
    root = tmp_path / "broken"
    _copy_package(package, root, manifest=man)

    with pytest.raises(mapfmt.PackageError, match="city_layer 'ghost'"):
        mapfmt.load_package(root)


def test_load_package_rejects_a_city_layer_that_isnt_a_point_identity_layer(
    package, tmp_path
):
    man = dict(package.manifest)
    man["city_layer"] = "terrain"  # brush/mask, not point/identity
    root = tmp_path / "broken"
    _copy_package(package, root, manifest=man)

    with pytest.raises(mapfmt.PackageError, match="must be input=point"):
        mapfmt.load_package(root)


def _copy_package(package, dest_root, manifest=None):
    import shutil

    shutil.copytree(package.root, dest_root)
    if manifest is not None:
        (dest_root / mapfmt.MANIFEST_NAME).write_text(json.dumps(manifest))


# ---------------------------------------------------------------------------
# project loading
# ---------------------------------------------------------------------------


def test_load_project_returns_an_empty_project_when_none_saved(package):
    project = mapfmt.load_project(package.root, package)
    assert project["layers"]["provinces"]["features"] == []


def test_load_project_rejects_a_stale_format_version(package):
    path = package.root / mapfmt.PROJECT_NAME
    path.write_text(json.dumps({"format_version": 0}))
    with pytest.raises(mapfmt.PackageError, match="format_version"):
        mapfmt.load_project(package.root, package)


def test_load_project_backfills_a_layer_added_after_the_project_was_saved(
    package, tmp_path
):
    project = mapfmt.empty_project(package.size, package)
    del project["layers"]["terrain"]
    mapfmt.save_project(package.root, project)

    reloaded = mapfmt.load_project(package.root, package)
    assert "terrain" in reloaded["layers"]


def test_read_json_reports_a_missing_file_as_a_package_error(tmp_path):
    with pytest.raises(mapfmt.PackageError, match="missing"):
        mapfmt._read_json(tmp_path / "nope.json")


def test_read_json_reports_malformed_json_as_a_package_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(mapfmt.PackageError, match="isn't valid JSON"):
        mapfmt._read_json(bad)
