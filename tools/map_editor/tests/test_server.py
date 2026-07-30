"""The editor's HTTP layer.

Mostly thin routing, with one piece of real logic. Brush rasters coming
back from the browser have to snap onto exact legend colors before they
touch the package.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import mapfmt
import numpy as np
import pytest
import server
from tests.conftest import box, make_package, project_with


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


# ---------------------------------------------------------------------------
# quantize_to_legend
# ---------------------------------------------------------------------------


def test_antialiased_brush_edges_snap_to_exact_legend_colors(package):
    """The canvas antialiases every stroke. Left alone those blended
    pixels are in no legend, so they'd mean nothing and render as noise."""
    cfg = package.layers["terrain"]
    plains = mapfmt.hex_to_rgb("#7d9a4e")

    raster = np.zeros((10, 10, 3), dtype=np.uint8)
    raster[5, 5] = plains
    raster[5, 6] = (0x6A, 0x6A, 0x6A)  # a real fringe color seen in the editor
    raster[5, 7] = (0x8A, 0x79, 0x59)

    out = server.quantize_to_legend(raster, cfg)

    legal = {(0, 0, 0)} | {mapfmt.hex_to_rgb(h) for h in cfg.legend}
    assert set(map(tuple, out.reshape(-1, 3))) <= legal
    assert tuple(out[5, 5]) == plains


def test_quantize_leaves_an_already_clean_raster_untouched(package):
    cfg = package.layers["terrain"]
    raster = np.zeros((4, 4, 3), dtype=np.uint8)
    raster[1, 1] = mapfmt.hex_to_rgb("#6b6b6b")
    assert np.array_equal(server.quantize_to_legend(raster, cfg), raster)


def test_quantize_keeps_unpainted_pixels_as_nodata(package):
    cfg = package.layers["terrain"]
    raster = np.zeros((4, 4, 3), dtype=np.uint8)
    out = server.quantize_to_legend(raster, cfg)
    assert (out == 0).all()


# ---------------------------------------------------------------------------
# the manifest the whole UI comes from
# ---------------------------------------------------------------------------


def test_manifest_lists_layers_in_draw_order(package):
    payload = server.manifest_payload(package)
    assert payload["layer_order"] == [
        "coastline",
        "provinces",
        "terrain",
        "resources",
        "ownership",
    ]
    assert payload["size"] == list(package.size)
    assert payload["province_layer"] == "provinces"


def test_a_layer_can_only_snap_to_snap_sources_drawn_before_it(package):
    payload = server.manifest_payload(package)
    assert payload["layers"]["coastline"]["snap_candidates"] == []
    assert payload["layers"]["provinces"]["snap_candidates"] == ["coastline"]
    assert payload["layers"]["terrain"]["snap_candidates"] == [
        "coastline",
        "provinces",
    ]


def test_manifest_carries_each_layer_legend_so_the_ui_needs_no_hardcoding(package):
    payload = server.manifest_payload(package)
    keys = {e["key"] for e in payload["layers"]["terrain"]["legend"].values()}
    assert "mountains" in keys
    assert payload["layers"]["ownership"]["input"] == "assign"


# ---------------------------------------------------------------------------
# fill gaps
# ---------------------------------------------------------------------------


def test_fill_gaps_returns_editable_geometry_and_reports_what_it_closed(package):
    gapped = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 28, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(32, 8, 50, 32)]},
    ]
    project = project_with(package, provinces=gapped)

    result = server.fill_gaps(project, package, "provinces")

    assert result["changed_px"] > 0
    assert result["residual_px"] == 0
    assert {f["id"] for f in result["features"]} == {1, 2}
    for feature in result["features"]:
        assert feature["polygons"], "gap-fill must hand back drawable rings"
        assert feature["name"] in ("West", "East"), "names must survive the round trip"


def test_fill_gaps_reports_land_too_wide_to_bridge(package):
    """A 12px fill limit can't close a hole the size of an untraced
    province. Silently annexing it would be worse than saying so."""
    sparse = [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 18, 32)]},
    ]
    project = project_with(package, provinces=sparse)

    result = server.fill_gaps(project, package, "provinces")
    assert result["residual_px"] > 0


def test_fill_gaps_refuses_a_brush_layer(package):
    project = project_with(package, provinces=two_provinces())
    with pytest.raises(server.ApiError, match="brush"):
        server.fill_gaps(project, package, "terrain")


def test_fill_gaps_on_an_unknown_layer_is_reported_not_crashed(package):
    project = project_with(package, provinces=two_provinces())
    with pytest.raises(server.ApiError, match="no layer named"):
        server.fill_gaps(project, package, "nonsense")


# ---------------------------------------------------------------------------
# revectorize
# ---------------------------------------------------------------------------


def test_revectorize_keeps_an_enclave_as_its_own_ring(package):
    """RETR_EXTERNAL would swallow the inner province; RETR_CCOMP doesn't."""
    raster = np.zeros((40, 60, 3), dtype=np.uint8)
    raster[8:32, 10:50] = mapfmt.id_to_color(2)
    raster[14:24, 20:30] = mapfmt.id_to_color(1)

    features = server.revectorize(raster, package.layers["provinces"], {})
    by_id = {f["id"]: f for f in features}

    assert set(by_id) == {1, 2}
    assert len(by_id[2]["polygons"]) == 2, "outer boundary plus the hole"


# ---------------------------------------------------------------------------
# point layer endpoint
# ---------------------------------------------------------------------------

CITIES_LAYER = {
    "name": "cities",
    "title": "Cities",
    "input": "point",
    "kind": "identity",
    "raster": "cities.png",
    "nodata_color": "#000000",
    "snap_source": False,
}


def _live_server(package_dir):
    handler = server.make_handler(package_dir)
    httpd = ThreadingHTTPServer(("localhost", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_points_endpoint_round_trips_a_provinces_point(tmp_path):
    package = make_package(tmp_path, extra_layers={"cities": CITIES_LAYER})
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    httpd = _live_server(package.root)
    try:
        conn = http.client.HTTPConnection("localhost", httpd.server_port)
        try:
            conn.request(
                "POST",
                "/api/layer/cities/points",
                body=json.dumps({"1": [20, 20]}),
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 200
            assert body["ok"] is True
            assert body["points"]["1"] == [20, 20]

            conn.request("GET", "/api/layer/cities/points")
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert body["1"] == [20, 20]
        finally:
            conn.close()
    finally:
        httpd.shutdown()


def test_points_endpoint_rejects_a_non_point_layer(tmp_path):
    package = make_package(tmp_path, extra_layers={"cities": CITIES_LAYER})
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)

    httpd = _live_server(package.root)
    try:
        conn = http.client.HTTPConnection("localhost", httpd.server_port)
        try:
            conn.request(
                "POST",
                "/api/layer/terrain/points",
                body=json.dumps({"1": [20, 20]}),
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 400
            assert body["ok"] is False
        finally:
            conn.close()
    finally:
        httpd.shutdown()
