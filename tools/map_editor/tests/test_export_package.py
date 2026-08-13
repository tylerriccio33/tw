"""The export pipeline.

Rasterizing layers, then clipping and gap-filling them against the
authored coastline. Then reducing painted layers into the per-province
table the simulation reads.
"""

import export
import mapfmt
import numpy as np
import pytest
from PIL import Image
from tests.conftest import box, make_package, paint, project_with

PLAINS = "#7d9a4e"
HILLS = "#8a7a5a"
IRON = "#9aa0a6"
GOLD = "#d4af37"

LAND = (10, 8, 50, 32)

# A synthetic brush layer with an "any"-mode reduce, standing in for the
# kind of painted deposit layer that reduce targets. (The real resources
# layer is now landmark points, like cities, so it no longer reduces into
# a per-province tag - see init_package.py.)
ORE_LAYER = {
    "name": "ore",
    "title": "Ore",
    "input": "brush",
    "kind": "class",
    "raster": "ore.png",
    "nodata_color": "#000000",
    "default_key": "iron",
    "snap_source": False,
    "clip_to": "coastline:land",
    "legend": {
        IRON: {"key": "iron", "name": "Iron"},
        GOLD: {"key": "gold", "name": "Gold"},
    },
    "reduce": {"into": "ore", "mode": "any"},
}


def two_provinces():
    """West and east halves of the land rectangle, sharing a vertical
    border down the middle."""
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


def run_export(package, project):
    return export.export_package(project, package)


# ---------------------------------------------------------------------------
# identity raster
# ---------------------------------------------------------------------------


def test_province_ids_survive_the_raster_round_trip(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    assert set(np.unique(ids).tolist()) == {0, 1, 2}
    assert ids[20, 15] == 1
    assert ids[20, 45] == 2


def test_rgb24_encoding_survives_ids_above_255():
    # Province 300 is #00012c - the encoding has to carry into the green
    # channel, which a naive "id goes in blue" scheme would silently wrap.
    assert mapfmt.id_to_color(300) == (0, 1, 44)
    assert mapfmt.color_to_id(mapfmt.id_to_color(300)) == 300
    assert mapfmt.color_to_id(mapfmt.id_to_color(16777215)) == 16777215


def test_province_id_zero_is_rejected_as_nodata():
    with pytest.raises(mapfmt.PackageError):
        mapfmt.id_to_color(0)


# ---------------------------------------------------------------------------
# clipping and gapfill
# ---------------------------------------------------------------------------


def test_province_drawn_over_the_sea_is_clipped_back_to_the_shore(package):
    sprawling = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(0, 0, 30, 40)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=sprawling)
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    assert ids[2, 2] == 0, "province painted over open sea should be erased"
    assert ids[20, 15] == 1, "the part on land stays"
    # 20 wide x 24 tall of the land rectangle
    assert table[1]["area_px"] == 20 * 24


def test_gapfill_closes_gaps_in_two_different_layers_independently(package):
    # A gap in the identity-kind provinces layer (a seam between two hand
    # drawn provinces) and a gap in the class-kind terrain brush layer
    # (an unpainted strip on land) both exist in the same export at once.
    # export.py's pipeline runs one loop over every layer, filling and
    # clipping each in turn against the shared coastline mask - this
    # confirms terrain's gapfill neither leaks into the provinces raster
    # nor gets confused by it, and vice versa. Single-layer gapfill is
    # already covered in isolation by test_gapfill.py and the seam test
    # just above/below this one; this is the "two layers, same export"
    # gap the plan calls out.
    gapped_provinces = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 28, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(32, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=gapped_provinces)
    # Terrain painted on both sides of a 4px unpainted strip at x=28..32 -
    # the same seam location as the province gap, so a leak between the
    # two layers' gapfills would be maximally likely to show up here.
    paint(package, "terrain", PLAINS, (10, 8, 26, 32))
    paint(package, "terrain", HILLS, (34, 8, 50, 32))

    run_export(package, project)

    with Image.open(package.raster_path("provinces")) as im:
        province_ids = export.id_buffer(np.array(im.convert("RGB")))
    with Image.open(package.raster_path("terrain")) as im:
        terrain_rgb = np.array(im.convert("RGB"))

    land_ids = province_ids[8:32, 10:50]
    assert not (land_ids == 0).any(), "province gap must be fully closed"
    assert set(np.unique(land_ids).tolist()) == {1, 2}

    plains_rgb = mapfmt.hex_to_rgb(PLAINS)
    hills_rgb = mapfmt.hex_to_rgb(HILLS)
    terrain_land = terrain_rgb[8:32, 10:50]
    assert np.all(
        np.any(
            [np.all(terrain_land == c, axis=-1) for c in (plains_rgb, hills_rgb)],
            axis=0,
        )
    ), "terrain gap must be fully closed too, independent of the province gap"

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    # The province boundary sits inside the filled terrain gap. Both
    # provinces' terrain tag must reflect their own dominant color, not
    # bleed the neighbor's gapfilled strip in.
    assert table[1]["tags"]["terrain"] == "plains"
    assert table[2]["tags"]["terrain"] == "hills"


def test_gap_between_two_provinces_is_filled_not_left_as_a_seam(package):
    # A 4px gutter between the two provinces - the kind of seam that comes
    # from tracing two borders separately instead of snapping them.
    gapped = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 28, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(32, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=gapped)
    run_export(package, project)

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    land_ids = ids[8:32, 10:50]
    assert not (land_ids == 0).any(), "no unclaimed land should remain in the gap"
    assert set(np.unique(land_ids).tolist()) == {1, 2}


def test_gapfill_does_not_leak_into_the_sea(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    with Image.open(package.raster_path("provinces")) as im:
        ids = export.id_buffer(np.array(im.convert("RGB")))

    assert (ids[:8, :] == 0).all(), "sea above the land stays unclaimed"
    assert (ids[32:, :] == 0).all()


def test_province_wholly_on_water_blocks_the_export(package):
    drowned = two_provinces() + [
        {"id": 3, "key": "atlantis", "name": "Atlantis", "polygons": [box(0, 0, 6, 6)]}
    ]
    project = project_with(package, provinces=drowned)

    with pytest.raises(export.ExportBlocked, match="Atlantis"):
        run_export(package, project)


# ---------------------------------------------------------------------------
# adjacency
# ---------------------------------------------------------------------------


def test_provinces_sharing_a_border_are_neighbors(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["neighbors"] == [2]
    assert table[2]["neighbors"] == [1]


def test_three_province_strip_only_links_actual_neighbors(package):
    strip = [
        {"id": 1, "key": "a", "name": "A", "polygons": [box(10, 8, 23, 32)]},
        {"id": 2, "key": "b", "name": "B", "polygons": [box(23, 8, 37, 32)]},
        {"id": 3, "key": "c", "name": "C", "polygons": [box(37, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=strip)
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["neighbors"] == [2]
    assert table[2]["neighbors"] == [1, 3]
    assert table[3]["neighbors"] == [2], "the far ends of the strip don't touch"


def test_provinces_touching_only_at_a_corner_are_not_neighbors(package):
    # A checkerboard: 1 and 4 meet at exactly one corner. An army can't
    # cross a point, so that must not become an edge in the graph.
    quads = [
        {"id": 1, "key": "nw", "name": "NW", "polygons": [box(10, 8, 30, 20)]},
        {"id": 2, "key": "ne", "name": "NE", "polygons": [box(30, 8, 50, 20)]},
        {"id": 3, "key": "sw", "name": "SW", "polygons": [box(10, 20, 30, 32)]},
        {"id": 4, "key": "se", "name": "SE", "polygons": [box(30, 20, 50, 32)]},
    ]
    project = project_with(package, provinces=quads)
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert 4 not in table[1]["neighbors"]
    assert 1 not in table[4]["neighbors"]
    assert table[1]["neighbors"] == [2, 3]


# ---------------------------------------------------------------------------
# reduce: painted layers -> province tags
# ---------------------------------------------------------------------------


def test_majority_reduce_tags_a_province_with_its_dominant_terrain(package):
    project = project_with(package, provinces=two_provinces())
    paint(package, "terrain", PLAINS, LAND)
    paint(package, "terrain", HILLS, (10, 8, 26, 32))  # most of the west province

    run_export(package, project)
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}

    assert table[1]["tags"]["terrain"] == "hills"
    assert table[2]["tags"]["terrain"] == "plains"


def test_any_reduce_lists_every_resource_present(tmp_path):
    package = make_package(tmp_path, extra_layers={"ore": ORE_LAYER})
    project = project_with(package, provinces=two_provinces())
    paint(package, "ore", IRON, (12, 10, 22, 30))
    paint(package, "ore", GOLD, (24, 10, 28, 30))

    run_export(package, project)
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}

    assert table[1]["tags"]["ore"] == ["gold", "iron"]
    assert table[2]["tags"]["ore"] == []


def test_any_reduce_ignores_a_few_pixels_bleeding_over_a_border(tmp_path):
    package = make_package(tmp_path, extra_layers={"ore": ORE_LAYER})
    project = project_with(package, provinces=two_provinces())
    paint(package, "ore", IRON, (12, 10, 22, 30))
    paint(package, "ore", IRON, (30, 10, 31, 11))  # 1px spill into the east

    run_export(package, project)
    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}

    assert table[1]["tags"]["ore"] == ["iron"]
    assert table[2]["tags"]["ore"] == [], "a single stray pixel isn't a deposit"


def test_starting_owner_comes_from_the_assign_layer_not_a_reduced_raster(package):
    project = project_with(
        package, provinces=two_provinces(), assignments={"1": "red", "2": "blue"}
    )
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["starting_owner"] == "red"
    assert table[2]["starting_owner"] == "blue"
    assert "starting_owner" not in table[1]["tags"], (
        "ownership is a column, not a tag - reducing its own raster back "
        "into tags would duplicate it"
    )


# ---------------------------------------------------------------------------
# geometry output
# ---------------------------------------------------------------------------


def test_geo_rings_are_traced_from_the_final_raster(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    geo = mapfmt.read_province_geo(package.root)
    assert geo["size"] == list(package.size)
    rings = {g["id"]: g["rings"] for g in geo["provinces"]}
    assert len(rings) == 2

    for province_rings in rings.values():
        assert len(province_rings) == 1
        ring = province_rings[0]
        assert ring["hole"] is False
        assert len(ring["points"]) >= 6  # at least 3 points, flattened x,y
        assert len(ring["points"]) % 2 == 0


def test_an_enclave_becomes_a_hole_ring_not_a_lost_province(package):
    # East completely surrounds a small West. RETR_EXTERNAL - what the
    # earlier raster importer used - would drop the hole and hand East
    # solid geometry covering land it doesn't own.
    enclosed = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(20, 14, 30, 24)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(10, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=enclosed)
    # East is drawn second, so draw West after it by reversing the order.
    project["layers"]["provinces"]["features"] = [enclosed[1], enclosed[0]]
    run_export(package, project)

    geo = {
        g["id"]: g["rings"] for g in mapfmt.read_province_geo(package.root)["provinces"]
    }
    assert any(ring["hole"] for ring in geo[2]), "the enclave must punch a hole"
    assert len(geo[1]) == 1


def test_centroid_is_the_area_centroid_of_the_province(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    cx, cy = table[1]["centroid"]
    assert 19 <= cx <= 21, "west province spans x 10..30"
    assert 19 <= cy <= 21, "land spans y 8..32"


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def test_export_writes_one_raster_per_declared_layer(package):
    project = project_with(package, provinces=two_provinces())
    run_export(package, project)

    for name in package.layer_order:
        assert package.raster_path(name).is_file(), f"{name} raster missing"
    assert (package.root / mapfmt.TABLE_NAME).is_file()
    assert (package.root / mapfmt.GEO_NAME).is_file()


def test_ownership_raster_is_painted_per_province(package):
    project = project_with(
        package, provinces=two_provinces(), assignments={"1": "red", "2": "blue"}
    )
    run_export(package, project)

    with Image.open(package.raster_path("ownership")) as im:
        raster = np.array(im.convert("RGB"))

    red = mapfmt.hex_to_rgb(
        next(f["color"] for f in package.factions if f["key"] == "red")
    )
    assert tuple(raster[20, 15]) == red


def test_a_blocked_export_writes_nothing(package):
    torn = [
        {
            "id": 1,
            "key": "bowtie",
            "name": "Bowtie",
            "polygons": [[[10, 8], [30, 32], [30, 8], [10, 32]]],
        }
    ]
    project = project_with(package, provinces=torn)

    with pytest.raises(export.ExportBlocked):
        run_export(package, project)

    assert not (package.root / mapfmt.TABLE_NAME).exists()
    assert not (package.root / mapfmt.GEO_NAME).exists()


# ---------------------------------------------------------------------------
# point-input city layer
#
# init_package's default layer set now includes "cities", a
# point_coupling=free layer whose "tier" field drives province growth
# (see growth.py, test_growth.py). It isn't coupled to a province at all,
# so export needs no province for a city point to be valid.
# ---------------------------------------------------------------------------


def test_city_layer_rasterizes_a_dot_colored_by_tier(package):
    project = project_with(package, provinces=two_provinces())
    project["layers"]["cities"]["points"] = {
        "p1": {"x": 20, "y": 20, "name": "Alpha", "tier": 1},
        "p2": {"x": 40, "y": 20, "name": "Beta", "tier": 5},
    }

    run_export(package, project)

    with Image.open(package.raster_path("cities")) as im:
        raster = np.array(im.convert("RGB"))

    tier_cfg = package.layers["cities"]
    assert tuple(raster[20, 20]) == mapfmt.hex_to_rgb(tier_cfg.color_for_key("1"))
    assert tuple(raster[20, 40]) == mapfmt.hex_to_rgb(tier_cfg.color_for_key("5"))
    assert tuple(raster[0, 0]) == (0, 0, 0)


def test_city_layer_bad_tier_blocks_export(package):
    project = project_with(package, provinces=two_provinces())
    project["layers"]["cities"]["points"] = {"p1": {"x": 20, "y": 20, "tier": 9}}

    with pytest.raises(export.ExportBlocked, match="tier"):
        run_export(package, project)


def test_province_table_gets_city_position_from_growth_seed(package):
    """A city point isn't linked to any province without a growth run.
    It's an independent seed, not one-per-province like a
    click-a-province point. Once growth.start records which city
    seeded which province, export's table carries that position
    through."""
    import growth

    project = project_with(package, provinces=[])
    project["layers"]["coastline"]["features"] = [
        {"key": "land", "polygons": [box(10, 8, 50, 32)]}
    ]
    project["layers"]["cities"]["points"] = {
        "p1": {"x": 20, "y": 20, "name": "Alpha", "tier": 1},
        "p2": {"x": 40, "y": 20, "name": "Beta", "tier": 1},
    }

    growth.start(package, project)
    run_export(package, project)

    table = {row["id"]: row for row in mapfmt.read_province_table(package.root)}
    assert table[1]["city_position"] == [20, 20]
    assert table[2]["city_position"] == [40, 20]
