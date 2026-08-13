"""The lon/lat <-> pixel calibration shared by every geo-driven import."""

import geo
import pytest
from geo import GeoRef

BBOX = (-11.0, 49.0, 3.0, 61.0)
SIZE = (5656, 8000)


def test_corners_map_to_pixel_extents():
    g = GeoRef(BBOX, SIZE)
    assert g.lonlat_to_pixel(-11.0, 61.0) == (0.0, 0.0)  # top-left
    assert g.lonlat_to_pixel(3.0, 49.0) == pytest.approx(SIZE)  # bottom-right


def test_latitude_grows_downward_in_pixels():
    g = GeoRef(BBOX, SIZE)
    _, y_north = g.lonlat_to_pixel(0.0, 60.0)
    _, y_south = g.lonlat_to_pixel(0.0, 50.0)
    assert y_north < y_south


def test_round_trips():
    g = GeoRef(BBOX, SIZE)
    for lon, lat in [(-0.128, 51.507), (-3.188, 55.953), (-0.370, 49.183)]:
        x, y = g.lonlat_to_pixel(lon, lat)
        back = g.pixel_to_lonlat(x, y)
        assert back == pytest.approx((lon, lat))


def test_contains_lonlat():
    g = GeoRef(BBOX, SIZE)
    assert g.contains_lonlat(-0.128, 51.507)
    assert not g.contains_lonlat(10.0, 51.5)  # east of the box


def test_from_manifest_reads_georef_block():
    manifest = {"georef": {"projection": "equirectangular", "bbox": list(BBOX)}}
    g = GeoRef.from_manifest(manifest, SIZE)
    assert g.bbox == BBOX
    assert g.size == SIZE


def test_from_manifest_without_georef_is_an_error():
    with pytest.raises(geo.GeoRefError):
        GeoRef.from_manifest({"size": list(SIZE)}, SIZE)


def test_bad_bbox_rejected():
    with pytest.raises(geo.GeoRefError):
        GeoRef((3.0, 49.0, -11.0, 61.0), SIZE)  # lon_max < lon_min


def test_unknown_projection_rejected():
    with pytest.raises(geo.GeoRefError):
        GeoRef(BBOX, SIZE, projection="mercator")
