"""The editor's HTTP layer.

Mostly thin routing, with one piece of real logic. Brush rasters coming
back from the browser have to snap onto exact legend colors before they
touch the package.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import export
import init_package
import mapfmt
import numpy as np
import pytest
import server
from PIL import Image
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
        "cities",
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

    features = export.revectorize(raster, package.layers["provinces"], {})
    by_id = {f["id"]: f for f in features}

    assert set(by_id) == {1, 2}
    assert len(by_id[2]["polygons"]) == 2, "outer boundary plus the hole"


def test_revectorize_handles_a_mask_layer_keyed_by_legend_entry(package):
    """A brush/mask layer (e.g. terrain) revectorizes by legend key, not id,
    and carries over any prior feature fields for that key."""
    cfg = package.layers["terrain"]
    plains_hex = next(iter(cfg.legend))
    plains_rgb = mapfmt.hex_to_rgb(plains_hex)

    raster = np.zeros((40, 60, 3), dtype=np.uint8)
    raster[:] = np.array(cfg.nodata_rgb, dtype=np.uint8)
    raster[10:30, 15:45] = plains_rgb

    key = cfg.legend[plains_hex]["key"]
    prior_project = {
        "layers": {"terrain": {"features": [{"key": key, "note": "prior"}]}}
    }

    features = export.revectorize(raster, cfg, prior_project)
    by_key = {f["key"]: f for f in features}

    assert key in by_key
    assert by_key[key]["note"] == "prior"
    assert by_key[key]["polygons"]


# ---------------------------------------------------------------------------
# point layer endpoint
# ---------------------------------------------------------------------------


def _live_server(package_dir):
    handler = server.make_handler(package_dir)
    httpd = ThreadingHTTPServer(("localhost", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_points_endpoint_round_trips_a_provinces_point(tmp_path):
    package = make_package(tmp_path)
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
    package = make_package(tmp_path)
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


# ---------------------------------------------------------------------------
# starting owners (factions)
# ---------------------------------------------------------------------------


def test_set_factions_adds_a_new_owner_to_every_assign_layer(package, tmp_path):
    project = project_with(package, provinces=two_provinces(), assignments={"1": "red"})
    factions = package.factions + [
        {"key": "purple", "name": "Purple", "color": "#800080", "money": 100}
    ]

    server.set_factions(package, project, factions)

    reloaded = mapfmt.load_package(package.root)
    assert "purple" in reloaded.layers["ownership"].keys
    assert reloaded.layers["ownership"].color_for_key("purple") == "#800080"
    assert json.loads((package.root / "factions.json").read_text()) == factions


def test_set_factions_prunes_assignments_for_a_deleted_owner(package):
    project = project_with(
        package, provinces=two_provinces(), assignments={"1": "red", "2": "blue"}
    )
    remaining = [f for f in package.factions if f["key"] != "red"]

    server.set_factions(package, project, remaining)

    saved = mapfmt.load_project(package.root, mapfmt.load_package(package.root))
    assert saved["layers"]["ownership"]["assignments"] == {"2": "blue"}


def test_set_factions_rejects_a_duplicate_key(package):
    project = project_with(package, provinces=two_provinces())
    factions = package.factions + [dict(package.factions[0], color="#123456")]

    with pytest.raises(server.ApiError, match="duplicate"):
        server.set_factions(package, project, factions)


def test_set_factions_rejects_a_shared_color(package):
    project = project_with(package, provinces=two_provinces())
    factions = package.factions + [
        {"key": "purple", "name": "Purple", "color": package.factions[0]["color"]}
    ]

    with pytest.raises(server.ApiError, match="color"):
        server.set_factions(package, project, factions)


def test_set_factions_rejects_an_empty_roster(package):
    project = project_with(package, provinces=two_provinces())

    with pytest.raises(server.ApiError, match="non-empty"):
        server.set_factions(package, project, [])


def test_set_factions_rejects_a_malformed_color(package):
    project = project_with(package, provinces=two_provinces())

    with pytest.raises(mapfmt.PackageError):
        server.set_factions(
            package, project, [{"key": "red", "name": "Red", "color": "not-a-color"}]
        )


def test_factions_endpoint_round_trips_over_http(tmp_path):
    package = make_package(tmp_path)
    project = project_with(
        package, provinces=two_provinces(), assignments={"1": "red", "2": "blue"}
    )
    mapfmt.save_project(package.root, project)

    httpd = _live_server(package.root)
    try:
        conn = http.client.HTTPConnection("localhost", httpd.server_port)
        try:
            new_roster = [f for f in init_package.FACTIONS if f["key"] != "red"] + [
                {"key": "purple", "name": "Purple", "color": "#800080", "money": 100}
            ]
            conn.request(
                "POST",
                "/api/factions",
                body=json.dumps({"factions": new_roster}),
            )
            resp = conn.getresponse()
            body = json.loads(resp.read())
            assert resp.status == 200
            assert body["ok"] is True
            assert {f["key"] for f in body["factions"]} == {
                "blue",
                "green",
                "yellow",
                "purple",
            }

            conn.request("GET", "/api/manifest")
            resp = conn.getresponse()
            manifest = json.loads(resp.read())
            assert {f["key"] for f in manifest["factions"]} == {
                "blue",
                "green",
                "yellow",
                "purple",
            }
            legend_keys = {
                e["key"] for e in manifest["layers"]["ownership"]["legend"].values()
            }
            assert legend_keys == {"blue", "green", "yellow", "purple"}
        finally:
            conn.close()
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# the HTTP surface itself
# ---------------------------------------------------------------------------


class _Client:
    """Small wrapper so route tests read as one line per request instead of
    hand-rolling http.client boilerplate each time."""

    def __init__(self, port):
        self.port = port

    def get(self, path):
        conn = http.client.HTTPConnection("localhost", self.port)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read(), resp
        finally:
            conn.close()

    def post(self, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("localhost", self.port)
        try:
            conn.request("POST", path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read(), resp
        finally:
            conn.close()


@pytest.fixture
def client(tmp_path):
    package = make_package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)
    httpd = _live_server(package.root)
    try:
        yield _Client(httpd.server_port), package
    finally:
        httpd.shutdown()


def test_get_root_serves_the_editor_html(client):
    c, _ = client
    status, _body, resp = c.get("/")
    assert status == 200
    assert resp.getheader("Content-Type") == "text/html"


def test_get_static_asset(client):
    c, _package = client
    import os

    static_files = list(server.STATIC_DIR.rglob("*"))
    files = [p for p in static_files if p.is_file()]
    assert files, "expected at least one static asset to exist"
    rel = os.path.relpath(files[0], server.STATIC_DIR)
    status, _body, _ = c.get(f"/static/{rel}")
    assert status == 200


def test_get_missing_static_asset_is_404(client):
    c, _ = client
    status, _, _ = c.get("/static/does-not-exist.js")
    assert status == 404


def test_get_project_returns_saved_project(client):
    c, _ = client
    status, body, _ = c.get("/api/project")
    assert status == 200
    payload = json.loads(body)
    assert "layers" in payload


def test_get_points_for_unknown_layer_is_404(client):
    c, _ = client
    status, _, _ = c.get("/api/layer/nonsense/points")
    assert status == 404


def test_get_backdrop(client):
    c, _ = client
    status, body, _resp = c.get("/api/backdrop")
    assert status == 200
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_layer_raster_falls_back_to_blank_when_unpainted(client):
    c, package = client
    raster_path = package.raster_path("terrain")
    if raster_path.is_file():
        raster_path.unlink()
    status, _body, resp = c.get("/api/layer/terrain")
    assert status == 200
    assert resp.getheader("Content-Type") == "image/png"


def test_get_layer_raster_serves_existing_file(client):
    c, _package = client
    status, _body, _ = c.get("/api/layer/terrain.png")
    assert status == 200


def test_get_layer_raster_for_unknown_layer_is_404(client):
    c, _ = client
    status, _, _ = c.get("/api/layer/nonsense")
    assert status == 404


def test_get_unknown_path_is_404(client):
    c, _ = client
    status, _, _ = c.get("/api/nope")
    assert status == 404


def test_get_reports_package_error_as_500(client):
    c, package = client
    (package.root / mapfmt.MANIFEST_NAME).write_text("not json")
    status, body, _ = c.get("/api/manifest")
    assert status == 500
    assert "error" in json.loads(body)


def test_post_project_saves_to_disk(client):
    c, package = client
    new_project = mapfmt.load_project(package.root, package)
    new_project["layers"]["provinces"]["features"][0]["name"] = "Renamed"
    status, body, _ = c.post(
        "/api/project",
        body=json.dumps(new_project).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert status == 200
    assert json.loads(body)["ok"] is True
    reloaded = mapfmt.load_project(package.root, package)
    assert reloaded["layers"]["provinces"]["features"][0]["name"] == "Renamed"


def test_post_project_with_malformed_json_is_500(client):
    c, _ = client
    status, body, _ = c.post("/api/project", body=b"{not json")
    assert status == 500
    assert json.loads(body)["ok"] is False


def test_post_layer_raster_for_unknown_layer_is_400(client):
    c, _ = client
    status, body, _ = c.post("/api/layer/nonsense", body=b"")
    assert status == 400
    assert json.loads(body)["ok"] is False


def test_post_layer_raster_uploads_and_quantizes(client):
    import io

    c, package = client
    cfg = package.layers["terrain"]
    plains_rgb = mapfmt.hex_to_rgb(next(iter(cfg.legend)))
    raster = np.zeros((package.size[1], package.size[0], 3), dtype=np.uint8)
    raster[5:10, 5:10] = plains_rgb
    buf = io.BytesIO()
    Image.fromarray(raster).save(buf, format="PNG")

    status, body, _ = c.post("/api/layer/terrain", body=buf.getvalue())
    assert status == 200
    assert json.loads(body)["ok"] is True

    with Image.open(package.raster_path("terrain")) as im:
        saved = np.array(im.convert("RGB"))
    assert tuple(saved[7, 7]) == plains_rgb


def test_post_autotrace_returns_features(client):
    c, _ = client
    status, body, _ = c.post("/api/autotrace/coastline", body=b"")
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert isinstance(payload["features"], list)


def test_post_autotrace_on_a_non_mask_layer_is_400(client):
    c, _ = client
    status, _body, _ = c.post("/api/autotrace/provinces", body=b"")
    assert status == 400


def test_post_fillgaps_over_http(client):
    c, package = client
    project = mapfmt.load_project(package.root, package)
    status, body, _ = c.post(
        "/api/fillgaps",
        body=json.dumps({"project": project, "layer": "provinces"}).encode(),
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert "features" in payload


def test_post_export_succeeds_for_a_valid_project(client):
    c, package = client
    project = mapfmt.load_project(package.root, package)
    status, body, _ = c.post(
        "/api/export", body=json.dumps({"project": project}).encode()
    )
    assert status == 200
    assert json.loads(body)["ok"] is True
    assert (package.root / mapfmt.TABLE_NAME).is_file()


def test_post_export_reports_validation_problems_as_400(client):
    c, package = client
    project = mapfmt.load_project(package.root, package)
    project["layers"]["provinces"]["features"][0]["polygons"] = [
        [[10, 8], [30, 32], [30, 8], [10, 32]]  # bowtie
    ]
    status, body, _ = c.post(
        "/api/export", body=json.dumps({"project": project}).encode()
    )
    assert status == 400
    assert json.loads(body)["ok"] is False


def test_post_unknown_path_is_404(client):
    c, _ = client
    status, _, _ = c.post("/api/nope", body=b"")
    assert status == 404


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_exits_with_a_helpful_message_when_no_package_exists(tmp_path):
    import sys

    argv = sys.argv
    sys.argv = ["server.py", "--package-dir", str(tmp_path / "nowhere")]
    try:
        with pytest.raises(SystemExit, match="make map-package-init"):
            server.main()
    finally:
        sys.argv = argv


def test_main_starts_and_stops_the_server(tmp_path, monkeypatch, capsys):
    package = make_package(tmp_path)

    class FakeHTTPServer:
        def __init__(self, addr, handler):
            self.addr = addr

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeHTTPServer)

    import sys

    argv = sys.argv
    sys.argv = ["server.py", "--package-dir", str(package.root), "--port", "0"]
    try:
        server.main()  # returns normally: main() catches the KeyboardInterrupt
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "Map editor running" in out
