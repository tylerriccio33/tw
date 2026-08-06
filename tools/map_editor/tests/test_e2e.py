"""Browser-driven end-to-end tests over the real editor UI.

These are the only tests in the suite that click and drag in a real,
headless browser. Everything else calls server.py's functions directly.
That buys real coverage of static/editor.js's event wiring, at the cost
of browser launch overhead. So the whole module opts into the `slow`
marker, same as tests/test_realistic.py. The default `pytest` run
excludes it.

Each test drives one of editor.js's three input gestures (see its module
docstring): polygon trace, brush paint, and assign-by-click. The DOM
selectors and interaction model below come from reading static/editor.js
directly: boot(), renderLayerList(), renderTools(), onCanvasClick(),
onBrushDown/Move/Up(), assignProvince(), saveDraft(). Check that file
first if these tests ever need updating for a UI change.

Run just this file: `uv run pytest -q tests/test_e2e.py -m slow`.
Run everything including these: `uv run pytest -q -m ""`.
"""

import json
from io import BytesIO

import mapfmt
import numpy as np
import pytest
from PIL import Image
from tests.conftest import _live_server, box, make_package, project_with

pytestmark = pytest.mark.slow


def two_provinces():
    return [
        {"id": 1, "key": "west", "name": "West", "polygons": [box(10, 8, 30, 32)]},
        {"id": 2, "key": "east", "name": "East", "polygons": [box(30, 8, 50, 32)]},
    ]


@pytest.fixture
def live_editor(tmp_path):
    """A small synthetic package, served live.

    Its project starts empty - no provinces drawn yet. Paired with its
    manifest size for coordinate math."""
    package = make_package(tmp_path)
    httpd = _live_server(package.root)
    try:
        yield httpd.server_port, package
    finally:
        httpd.shutdown()


@pytest.fixture
def live_editor_with_provinces(tmp_path):
    """Same package, but pre-seeded with two provinces and no owners -
    the assign test needs something to click on."""
    package = make_package(tmp_path)
    project = project_with(package, provinces=two_provinces())
    mapfmt.save_project(package.root, project)
    httpd = _live_server(package.root)
    try:
        yield httpd.server_port, package
    finally:
        httpd.shutdown()


def goto_editor(page, port):
    page.goto(f"http://localhost:{port}/")
    # boot() fetches /api/manifest + /api/project then renders the sidebar;
    # waiting on a layer row is waiting on that round trip to finish.
    page.wait_for_selector("#layerList .layer-row")


def data_to_page_point(page, data_xy, size):
    """Translate a map-data coordinate into a page coordinate.

    Mirrors editor.js's own svgPoint(), which does the inverse (page ->
    data), scaled by #overlay's rendered bounding box."""
    box_ = page.locator("#overlay").bounding_box()
    width, height = size
    x, y = data_xy
    return box_["x"] + (x / width) * box_["width"], box_["y"] + (y / height) * box_[
        "height"
    ]


def select_layer(page, title):
    page.locator("#layerList .layer-row", has_text=title).locator(
        ".layer-title"
    ).click()


def test_polygon_trace_adds_a_new_province_feature(live_editor, page):
    port, package = live_editor
    goto_editor(page, port)

    select_layer(page, "Provinces")
    assert "No provinces yet" in page.locator("#featureList").inner_text()

    page.locator("#tools button", has_text="+ New Province").click()
    # showModal()'s in-page dialog, pre-filled with a default name.
    page.locator(".modal-overlay .ok").click()

    # A feature row now exists but has drawn nothing yet.
    rows = page.locator("#featureList .feature-row")
    assert rows.count() == 1
    assert "0" in rows.nth(0).locator(".shape-count").inner_text()

    for data_point in [(20, 15), (35, 15), (27, 25)]:
        px, py = data_to_page_point(page, data_point, package.size)
        page.mouse.click(px, py)
    page.keyboard.press("Enter")  # finishShape()

    assert "1" in rows.nth(0).locator(".shape-count").inner_text()


def test_brush_paint_changes_the_terrain_raster(live_editor, page):
    port, package = live_editor
    goto_editor(page, port)

    select_layer(page, "Terrain")
    page.locator("#tools button", has_text="Brush").click()

    # Drag a stroke across a patch of land (land_box defaults to
    # (10, 8, 50, 32) in tests/conftest.py's make_package).
    start = data_to_page_point(page, (20, 18), package.size)
    end = data_to_page_point(page, (26, 22), package.size)
    page.mouse.move(*start)
    page.mouse.down()
    for step in range(1, 6):
        t = step / 5
        page.mouse.move(
            start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
        )
    page.mouse.up()

    with page.expect_response(lambda r: r.url.endswith("/api/layer/terrain.png")):
        page.locator("#saveBtn").click()

    conn_url = f"http://localhost:{port}/api/layer/terrain.png"
    raw = page.request.get(conn_url).body()
    painted = np.array(Image.open(BytesIO(raw)).convert("RGB"))
    plains_rgb = mapfmt.hex_to_rgb("#7d9a4e")  # terrain's default_key color
    assert tuple(painted[20, 23]) == plains_rgb


def test_assign_click_sets_and_persists_a_starting_owner(
    live_editor_with_provinces, page
):
    port, package = live_editor_with_provinces
    goto_editor(page, port)

    select_layer(page, "Starting owners")
    # legendEntries(cfg)[0] is the first faction ("red") - default_key
    # isn't set on the ownership layer, so boot() falls back to it.
    assert "active" in (
        page.locator("#featureList .feature-row").first.get_attribute("class") or ""
    )

    # West province is box(10, 8, 30, 32); its centroid sits at (20, 20).
    # renderPolygonLayer() marks province polygons `.clickable` and wires
    # assignProvince() only while the ownership layer is active.
    page.wait_for_selector("#overlay polygon.clickable")
    px, py = data_to_page_point(page, (20, 20), package.size)
    page.mouse.click(px, py)

    with page.expect_response(lambda r: r.url.endswith("/api/project")):
        page.locator("#saveBtn").click()

    saved = json.loads((package.root / "project.json").read_text())
    assert saved["layers"]["ownership"]["assignments"] == {"1": "red"}
