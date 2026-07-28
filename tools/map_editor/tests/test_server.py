"""Tests for the map editor's server: project I/O, raster<->polygon
round-tripping, and the coastline classification/caching used for
border-snapping."""

import argparse
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

import server as srv


def make_image(path: Path, size=(40, 40), color=(200, 200, 200)):
    Image.new("RGB", size, color).save(path)


def make_two_tone_image(path: Path, width=200, height=100):
    """Left half warm/land-colored, right half cool/sea-colored, matching
    the BGR channel bias classify_coastline() looks for."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, : width // 2] = (40, 90, 150)  # BGR: land, red-biased
    img[:, width // 2 :] = (150, 90, 40)  # BGR: sea, blue-biased
    cv2.imwrite(str(path), img)


# ---------------------------------------------------------------------------
# export_project / import_from_raster
# ---------------------------------------------------------------------------


def test_export_project_writes_flat_raster_and_color_map(tmp_path):
    # Two-tone backdrop (land on the left, sea on the right) so gap-filling
    # only extends the polygon's region into land, leaving the sea side as
    # background - see test_export_project_fills_land_gaps_but_not_sea below
    # for the gap-filling behavior itself.
    image_path = tmp_path / "backdrop.png"
    make_two_tone_image(image_path, width=20, height=20)

    project = {
        "image_size": [20, 20],
        "regions": [
            {
                "name": "Northlands",
                "color": "#ff0000",
                "polygons": [[[0, 0], [10, 0], [10, 10], [0, 10]]],
            }
        ],
    }

    result = srv.export_project(project, image_path, tmp_path / "out")

    region_map = np.array(
        Image.open(tmp_path / "out" / "region_map.png").convert("RGB")
    )
    assert tuple(region_map[5, 5]) == (255, 0, 0)
    assert tuple(region_map[15, 15]) != (255, 0, 0)  # sea side, left as background

    names = json.loads((tmp_path / "out" / "regions.txt").read_text())
    assert names == {"#ff0000": "Northlands"}
    assert result["region_count"] == 1


def test_export_project_fills_land_gaps_but_not_sea(tmp_path):
    # Two adjacent land polygons with a 1px unassigned gap between them at
    # x=10, plus untouched background on the sea side (x>=100). Gap-filling
    # must cover the land gap with the nearest region's exact color and
    # leave the sea background alone.
    image_path = tmp_path / "backdrop.png"
    make_two_tone_image(image_path, width=200, height=100)

    project = {
        "image_size": [200, 100],
        "regions": [
            {
                "name": "West",
                "color": "#ff0000",
                "polygons": [[[0, 0], [9, 0], [9, 50], [0, 50]]],
            },
            {
                "name": "East",
                "color": "#00ff00",
                "polygons": [[[11, 0], [90, 0], [90, 50], [11, 50]]],
            },
        ],
    }

    srv.export_project(project, image_path, tmp_path / "out")

    region_map = np.array(
        Image.open(tmp_path / "out" / "region_map.png").convert("RGB")
    )
    # The 1px land gap at x=10 must take one of the two exact region
    # colors, not white and not a blend.
    gap_color = tuple(region_map[25, 10])
    assert gap_color in {(255, 0, 0), (0, 255, 0)}
    # Sea-side background (x=150, well past the land/sea split at x=100)
    # must stay untouched.
    assert tuple(region_map[25, 150]) == (255, 255, 255)


def test_export_project_clips_a_polygon_traced_over_the_sea(tmp_path):
    # An overly loose polygon spans the whole land/sea split (x=0..190 on
    # a 200px-wide two-tone backdrop where sea starts at x=100). Export
    # must clip the sea-side portion back to background, not ship a
    # region painted over open water.
    image_path = tmp_path / "backdrop.png"
    make_two_tone_image(image_path, width=200, height=100)

    project = {
        "image_size": [200, 100],
        "regions": [
            {
                "name": "Oversized",
                "color": "#ff00ff",
                "polygons": [[[0, 0], [190, 0], [190, 50], [0, 50]]],
            }
        ],
    }

    srv.export_project(project, image_path, tmp_path / "out")

    region_map = np.array(
        Image.open(tmp_path / "out" / "region_map.png").convert("RGB")
    )
    assert tuple(region_map[25, 20]) == (255, 0, 255)  # land side, kept
    assert tuple(region_map[25, 180]) == (255, 255, 255)  # sea side, clipped


def test_export_project_skips_unnamed_regions(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_image(image_path, size=(10, 10))
    project = {
        "image_size": [10, 10],
        "regions": [
            {"name": "  ", "color": "#00ff00", "polygons": [[[0, 0], [5, 0], [5, 5]]]}
        ],
    }

    result = srv.export_project(project, image_path, tmp_path / "out")

    assert result["region_count"] == 0
    names = json.loads((tmp_path / "out" / "regions.txt").read_text())
    assert names == {}


# ---------------------------------------------------------------------------
# validate_project / export_project rejection - the editor must not be able
# to produce a region_map.png + regions.txt that can't faithfully round-trip
# through province_map.gd: a dropped faction (color collision), a torn or
# pinched shape (self-intersecting or repeated-point polygon), or a border
# that reaches off the map.
# ---------------------------------------------------------------------------


def test_validate_project_flags_duplicate_color_across_regions():
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "France",
                "color": "#c65102",
                "polygons": [[[0, 0], [10, 0], [10, 10]]],
            },
            {
                "name": "Persia",
                "color": "#c65102",
                "polygons": [[[50, 50], [60, 50], [60, 60]]],
            },
        ],
    }

    problems = srv.validate_project(project, (100, 100))

    assert any("France" in p and "Persia" in p and "#c65102" in p for p in problems)


def test_validate_project_allows_same_color_reused_under_the_same_name():
    # Multiple polygons for one region under its own name/color is normal
    # (e.g. an archipelago); only a *different* name reusing a color is a
    # collision.
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "Italy",
                "color": "#bfef45",
                "polygons": [
                    [[0, 0], [10, 0], [10, 10]],
                    [[50, 50], [60, 50], [60, 60]],
                ],
            }
        ],
    }

    assert srv.validate_project(project, (100, 100)) == []


def test_validate_project_flags_self_intersecting_polygon():
    # A bowtie: edges (0,0)->(10,10) and (10,0)->(0,10) cross in the middle.
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "Bowtie",
                "color": "#123456",
                "polygons": [[[0, 0], [10, 0], [0, 10], [10, 10]]],
            }
        ],
    }

    problems = srv.validate_project(project, (100, 100))

    assert any("crosses itself" in p for p in problems)


def test_validate_project_flags_repeated_point_in_polygon():
    # Mirrors the real England bug: point 0 and a later point are the exact
    # same coordinate, pinching the polygon into a self-touching loop that
    # renders as a torn/disconnected fragment instead of one landmass.
    project = {
        "image_size": [300, 300],
        "regions": [
            {
                "name": "England",
                "color": "#c50202",
                "polygons": [
                    [
                        [174.0, 137.0],
                        [288.0, 114.0],
                        [231.7, 8.3],
                        [184.9, 3.6],
                        [212.0, 47.0],
                        [199.2, 56.9],
                        [181.0, 89.0],
                        [174.0, 137.0],  # same as point 0
                        [202.2, 96.8],
                    ]
                ],
            }
        ],
    }

    problems = srv.validate_project(project, (300, 300))

    assert any("revisits the same point" in p for p in problems)


def test_validate_project_flags_point_outside_the_map():
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "Overboard",
                "color": "#123456",
                "polygons": [[[50, 50], [120, 50], [90, 90]]],
            }
        ],
    }

    problems = srv.validate_project(project, (100, 100))

    assert any("outside the 100x100 map" in p for p in problems)


def test_validate_project_accepts_a_clean_project():
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "Clean",
                "color": "#123456",
                "polygons": [[[10, 10], [90, 10], [90, 90], [10, 90]]],
            }
        ],
    }

    assert srv.validate_project(project, (100, 100)) == []


def test_export_project_rejects_invalid_project_without_writing_files(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_image(image_path, size=(100, 100))
    project = {
        "image_size": [100, 100],
        "regions": [
            {
                "name": "France",
                "color": "#c65102",
                "polygons": [[[0, 0], [10, 0], [10, 10]]],
            },
            {
                "name": "Persia",
                "color": "#c65102",
                "polygons": [[[50, 50], [60, 50], [60, 60]]],
            },
        ],
    }

    with pytest.raises(ValueError, match="France"):
        srv.export_project(project, image_path, tmp_path / "out")

    # A rejected export must not leave a half-written/corrupt output behind.
    assert not (tmp_path / "out" / "region_map.png").exists()
    assert not (tmp_path / "out" / "regions.txt").exists()


def test_import_from_raster_recovers_region_and_color(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_image(image_path, size=(30, 30))

    project = {
        "image_size": [30, 30],
        "regions": [
            {
                "name": "Southreach",
                "color": "#0000ff",
                "polygons": [[[2, 2], [20, 2], [20, 20], [2, 20]]],
            }
        ],
    }
    srv.export_project(project, image_path, tmp_path / "out")

    recovered = srv.import_from_raster(tmp_path / "out", image_path)

    assert recovered is not None
    assert recovered["image_size"] == [30, 30]
    assert len(recovered["regions"]) == 1
    region = recovered["regions"][0]
    assert region["name"] == "Southreach"
    assert region["color"] == "#0000ff"

    # Traced polygon should still contain a point well inside the original.
    contour = np.array(region["polygons"][0], dtype=np.float32).reshape(-1, 1, 2)
    assert cv2.pointPolygonTest(contour, (10, 10), False) >= 0


def test_import_from_raster_missing_files_returns_none(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_image(image_path)
    assert srv.import_from_raster(tmp_path / "nowhere", image_path) is None


def test_import_from_raster_rescales_to_target_image_size(tmp_path):
    # Export at 30x30, then re-import against a 2x-larger target image;
    # points should land at ~2x their original coordinates.
    source_image = tmp_path / "small.png"
    make_image(source_image, size=(30, 30))
    project = {
        "image_size": [30, 30],
        "regions": [
            {
                "name": "A",
                "color": "#123456",
                "polygons": [[[4, 4], [20, 4], [20, 20], [4, 20]]],
            }
        ],
    }
    srv.export_project(project, source_image, tmp_path / "out")

    target_image = tmp_path / "big.png"
    make_image(target_image, size=(60, 60))

    recovered = srv.import_from_raster(tmp_path / "out", target_image)

    assert recovered["image_size"] == [60, 60]
    xs = [p[0] for p in recovered["regions"][0]["polygons"][0]]
    assert max(xs) > 30  # scaled up, not left at source resolution


# ---------------------------------------------------------------------------
# load_project priority chain
# ---------------------------------------------------------------------------


def test_load_project_prefers_existing_project_json(tmp_path):
    dev_dir = tmp_path / "dev"
    dev_dir.mkdir()
    project_path = dev_dir / "project.json"
    saved = {
        "image_size": [5, 5],
        "regions": [{"name": "X", "color": "#111111", "polygons": []}],
    }
    project_path.write_text(json.dumps(saved))

    image_path = tmp_path / "backdrop.png"
    make_image(image_path, size=(5, 5))

    loaded = srv.load_project(project_path, dev_dir, tmp_path / "game", image_path)
    assert loaded == saved


def test_load_project_falls_back_to_empty(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_image(image_path, size=(8, 8))

    loaded = srv.load_project(
        tmp_path / "dev" / "project.json",
        tmp_path / "dev",
        tmp_path / "game",
        image_path,
    )
    assert loaded == {"image_size": [8, 8], "regions": []}


# ---------------------------------------------------------------------------
# coastline classification + caching
# ---------------------------------------------------------------------------


def test_classify_coastline_finds_split_near_boundary(tmp_path):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    lines = srv.classify_coastline(image_path)

    assert lines, "expected at least one traced boundary"
    xs = [pt[0] for line in lines for pt in line]
    # The true land/sea split is at x=100; traced points should cluster
    # near it rather than at the canvas edges (0 or 200).
    assert any(80 <= x <= 120 for x in xs)


def test_build_land_mask_matches_known_land_and_sea_sides(tmp_path):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path, width=200, height=100)

    land_mask = srv.build_land_mask(image_path)

    assert land_mask.shape == (100, 200)
    assert land_mask.dtype == bool
    assert land_mask[50, 20]  # well inside the land (red-biased) half
    assert not land_mask[50, 180]  # well inside the sea (blue-biased) half


def test_load_or_build_coastline_caches_to_disk(tmp_path, monkeypatch):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path)
    cache_path = tmp_path / "coastline.json"

    calls = []
    real = srv.classify_coastline

    def counting_classify(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(srv, "classify_coastline", counting_classify)

    first = srv.load_or_build_coastline(image_path, cache_path)
    assert len(calls) == 1
    assert cache_path.is_file()

    second = srv.load_or_build_coastline(image_path, cache_path)
    assert len(calls) == 1, "second call should hit the on-disk cache, not reclassify"
    assert second == first


def test_load_or_build_coastline_invalidates_on_image_change(tmp_path, monkeypatch):
    image_path = tmp_path / "two_tone.png"
    make_two_tone_image(image_path)
    cache_path = tmp_path / "coastline.json"

    calls = []
    real = srv.classify_coastline
    monkeypatch.setattr(
        srv, "classify_coastline", lambda p: (calls.append(p), real(p))[1]
    )

    srv.load_or_build_coastline(image_path, cache_path)
    assert len(calls) == 1

    # Touch the image with a later mtime to simulate re-tracing after edits.
    import os
    import time

    time.sleep(0.01)
    make_two_tone_image(image_path, width=250)
    os.utime(image_path, None)

    srv.load_or_build_coastline(image_path, cache_path)
    assert len(calls) == 2, "changed source image should invalidate the cache"


# ---------------------------------------------------------------------------
# HTTP layer (routing, project persistence, export)
# ---------------------------------------------------------------------------


@pytest.fixture
def running_server(tmp_path):
    image_path = tmp_path / "backdrop.png"
    make_two_tone_image(image_path)
    dev_dir = tmp_path / "dev"
    game_dir = tmp_path / "game"

    args = argparse.Namespace(
        image=str(image_path), dev_dir=str(dev_dir), game_dir=str(game_dir)
    )
    handler = srv.make_handler(args)
    server = ThreadingHTTPServer(("localhost", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, dev_dir, game_dir
    finally:
        server.shutdown()
        thread.join()


def _get(server, path):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://localhost:{port}{path}") as resp:
        return resp.status, resp.read()


def _post(server, path, payload):
    port = server.server_address[1]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read())


def test_api_project_get_returns_empty_new_project(running_server):
    server, _dev_dir, _game_dir = running_server
    status, body = _get(server, "/api/project")
    assert status == 200
    assert json.loads(body)["regions"] == []


def test_api_project_post_then_get_roundtrips(running_server):
    server, dev_dir, _game_dir = running_server
    payload = {
        "image_size": [200, 100],
        "regions": [{"name": "R", "color": "#abcdef", "polygons": []}],
    }

    status, body = _post(server, "/api/project", payload)
    assert status == 200 and body["ok"] is True
    assert (dev_dir / "project.json").is_file()

    status, body = _get(server, "/api/project")
    assert json.loads(body) == payload


def test_api_export_writes_dev_files(running_server):
    server, dev_dir, _game_dir = running_server
    payload = {
        "image_size": [200, 100],
        "regions": [
            {"name": "R", "color": "#abcdef", "polygons": [[[0, 0], [10, 0], [10, 10]]]}
        ],
    }
    status, body = _post(server, "/api/export", payload)
    assert status == 200 and body["ok"] is True
    assert (dev_dir / "region_map.png").is_file()
    assert (dev_dir / "regions.txt").is_file()


def test_api_export_rejects_invalid_project_over_http(running_server):
    server, dev_dir, _game_dir = running_server
    payload = {
        "image_size": [200, 100],
        "regions": [
            {
                "name": "France",
                "color": "#c65102",
                "polygons": [[[0, 0], [10, 0], [10, 10]]],
            },
            {
                "name": "Persia",
                "color": "#c65102",
                "polygons": [[[50, 50], [60, 50], [60, 60]]],
            },
        ],
    }
    port = server.server_address[1]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://localhost:{port}/api/export",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
        assert False, "expected a 500 for an invalid project"
    except urllib.error.HTTPError as e:
        assert e.code == 500
        error_body = json.loads(e.read())
        assert error_body["ok"] is False
        assert "France" in error_body["error"]

    assert not (dev_dir / "region_map.png").exists()


def test_api_coastline_returns_lines_and_is_cached(running_server):
    server, dev_dir, _game_dir = running_server
    status, body = _get(server, "/api/coastline")
    assert status == 200
    data = json.loads(body)
    assert "lines" in data
    assert (dev_dir / "coastline.json").is_file()


def test_api_reload_from_game_404_without_a_game_map(running_server):
    server, _dev_dir, _game_dir = running_server
    port = server.server_address[1]
    try:
        urllib.request.urlopen(f"http://localhost:{port}/api/reload-from-game")
        assert False, "expected an HTTPError"
    except urllib.error.HTTPError as e:
        assert e.code == 404
