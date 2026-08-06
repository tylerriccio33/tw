"""Full-scale regressions against the real backdrop.

The synthetic packages in test_export_package.py use tidy rectangles.
This builds a package from the actual campaign backdrop, a real
coastline with islands, fjords and enclaves. It then asserts the
properties any map the game can run on has to hold.

Skips if the backdrop isn't present, so a checkout without game art
still runs the rest of the suite.
"""

import export
import init_package
import mapfmt
import numpy as np
import pytest
from PIL import Image

BACKDROP = init_package.GAME_DIR_DEFAULT / "backdrop.png"

pytestmark = [
    pytest.mark.skipif(not BACKDROP.is_file(), reason=f"no backdrop at {BACKDROP}"),
    pytest.mark.slow,
]


@pytest.fixture(scope="module")
def real_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("real_package")
    init_package.init_package(root, BACKDROP, province_count=12)
    package = mapfmt.load_package(root)
    project = mapfmt.load_project(root, package)
    export.export_package(project, package)
    return package, project


def test_every_province_survives_the_export(real_package):
    package, project = real_package
    drawn = {int(f["id"]) for f in mapfmt.project_features(project, "provinces")}
    exported = {row["id"] for row in mapfmt.read_province_table(package.root)}
    assert drawn == exported


def test_no_unclaimed_land_is_left_between_provinces(real_package):
    """The gapfill guarantee at full scale: after export, every land pixel
    belongs to some province. An unclaimed strip along a border is exactly
    the seam this pipeline exists to close."""
    package, _ = real_package

    with Image.open(package.raster_path("coastline")) as im:
        coast = np.array(im.convert("RGB"))
    land = np.all(coast == np.array(mapfmt.hex_to_rgb("#f2efe6"), np.uint8), axis=-1)

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    unclaimed = int((land & (ids == 0)).sum())
    assert unclaimed == 0, f"{unclaimed} land pixels belong to no province"


def test_no_province_claims_open_sea(real_package):
    package, _ = real_package

    with Image.open(package.raster_path("coastline")) as im:
        coast = np.array(im.convert("RGB"))
    land = np.all(coast == np.array(mapfmt.hex_to_rgb("#f2efe6"), np.uint8), axis=-1)

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    assert int(((ids > 0) & ~land).sum()) == 0


def test_province_areas_sum_to_the_landmass(real_package):
    package, _ = real_package
    table = mapfmt.read_province_table(package.root)

    with Image.open(package.raster_path("coastline")) as im:
        coast = np.array(im.convert("RGB"))
    land = int(
        np.all(coast == np.array(mapfmt.hex_to_rgb("#f2efe6"), np.uint8), axis=-1).sum()
    )

    assert sum(row["area_px"] for row in table) == land


def test_adjacency_is_symmetric_and_never_self_referential(real_package):
    package, _ = real_package
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}

    for pid, row in table.items():
        assert pid not in row["neighbors"], f"province {pid} borders itself"
        for neighbor in row["neighbors"]:
            assert neighbor in table, f"province {pid} borders unknown {neighbor}"
            assert pid in table[neighbor]["neighbors"], (
                f"{pid} lists {neighbor} but not the other way round"
            )


def test_the_mainland_provinces_form_one_connected_graph(real_package):
    """Islands stand alone legitimately. The bulk of the map still has to
    connect by land, or an army can never leave home."""
    package, _ = real_package
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}

    seen, stack = set(), [max(table, key=lambda pid: table[pid]["area_px"])]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(table[pid]["neighbors"])

    connected_area = sum(table[pid]["area_px"] for pid in seen)
    total_area = sum(row["area_px"] for row in table.values())
    assert connected_area / total_area > 0.9


def test_every_province_has_geometry_and_a_tag_for_every_reduced_layer(real_package):
    package, _ = real_package
    table = mapfmt.read_province_table(package.root)
    geo = {
        g["id"]: g["rings"] for g in mapfmt.read_province_geo(package.root)["provinces"]
    }

    expected_tags = {
        package.layers[name].reduce["into"]
        for name in package.layer_order
        if package.layers[name].reduce and package.layers[name].input != "assign"
    }

    for row in table:
        assert geo[row["id"]], f"province {row['id']} exported no rings"
        assert set(row["tags"]) == expected_tags
        assert row["starting_owner"] in {f["key"] for f in package.factions}


def test_geometry_matches_the_raster_it_was_traced_from(real_package):
    """provinces.geo.json is what Godot builds its click targets from. If
    it drifts from the raster, a click lands in the wrong province."""
    from PIL import ImageDraw

    package, _ = real_package
    width, height = package.size
    geo = mapfmt.read_province_geo(package.root)["provinces"]

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    for entry in geo[:4]:
        canvas = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(canvas)
        for ring in entry["rings"]:
            pts = list(zip(ring["points"][0::2], ring["points"][1::2]))
            draw.polygon(pts, fill=0 if ring["hole"] else 255)

        from_geo = np.array(canvas) > 0
        from_raster = ids == entry["id"]
        disagreement = int((from_geo ^ from_raster).sum())
        assert disagreement < 0.05 * int(from_raster.sum()), (
            f"province {entry['id']} geometry disagrees with its raster by "
            f"{disagreement}px"
        )
