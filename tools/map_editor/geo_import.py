"""Seed the coastline layer from real-world Natural Earth vector data.

Natural Earth ships pre-simplified land polygons in lon/lat degrees.
See: https://www.naturalearthdata.com/downloads/50m-physical-vectors/50m-land/

This script downloads (or reuses) that shapefile and clips it to a
chosen lon/lat bounding box. It linearly maps that box onto the map's
pixel canvas (size comes from map.json) and simplifies the result.
It then writes the rings into project.json's coastline feature. That's
the same "polygons" list the editor itself writes when you trace by hand.
Nothing downstream needs to know the rings came from real data instead
of a mouse. Re-open the editor and touch it up with Edit Vertices or
magnetic trace like any other layer.

This keeps only exterior rings. Natural Earth's land layer has a
handful of interior rings for inland seas (e.g. Caspian); this drops
those rather than silently painting them as land. Re-cut them by hand
if your bbox includes one.

Usage:
    uv run geo_import.py --bbox -11 49 3 61 \
        --project dev_map_data/project.json --simplify 0.02

    --bbox takes lon_min lat_min lon_max lat_max (degrees). Pick it by
    eye against your current backdrop. It does not need to be exact,
    since you can still hand-edit the result afterward.
"""

from __future__ import annotations

import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import shapefile  # pyshp
from geo import GeoRef
from PIL import Image, ImageDraw
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform

NE_LAND_URL_50M = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_land.zip"
NE_LAND_URL_10M = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"
NE_LAND_URL = NE_LAND_URL_10M

# Matches init_package.py's palette, so a generated backdrop passes
# coastline.assert_clean_source and autotraces the same shape back.
SEA_COLOR = "#1d3550"
LAND_COLOR = "#ffffff"  # must read >= coastline.LAND_MIN_GRAY for autotrace

CACHE_DIR = Path(__file__).parent / ".geo_cache"


def fetch_shapefile(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    """Download and extract a Natural Earth shapefile zip, cached by filename."""
    cache_dir.mkdir(exist_ok=True)
    name = url.rsplit("/", 1)[-1].removesuffix(".zip")
    extract_dir = cache_dir / name
    shp_path = extract_dir / f"{name}.shp"
    if shp_path.exists():
        return shp_path

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response:
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(extract_dir)
    return shp_path


def load_land_polygons(shp_path: Path, bbox: tuple[float, float, float, float]):
    """Read the shapefile, clip to bbox, return a list of shapely Polygons."""
    clip = box(*bbox)
    reader = shapefile.Reader(str(shp_path))
    polygons = []
    for record in reader.shapes():
        geom = shape(record.__geo_interface__)
        if not geom.intersects(clip):
            continue
        clipped = geom.intersection(clip)
        if clipped.is_empty:
            continue
        geoms = clipped.geoms if hasattr(clipped, "geoms") else [clipped]
        for g in geoms:
            if g.geom_type == "Polygon" and not g.is_empty:
                polygons.append(g)
    return polygons


def resolve_georef(bbox, manifest, size: tuple[int, int]) -> GeoRef:
    """Pick the calibration: an explicit --bbox wins; otherwise reuse the
    georef already persisted in map.json. Error if neither is available."""
    if bbox is not None:
        return GeoRef(bbox=tuple(bbox), size=size)
    if manifest is not None and manifest.get("georef"):
        return GeoRef.from_manifest(manifest, size)
    raise SystemExit(
        "no --bbox given and map.json has no georef to reuse; pass --bbox "
        "once to calibrate the map."
    )


def shapely_transform_fn(georef: GeoRef):
    """Adapt GeoRef.lonlat_to_pixel to shapely's transform (which may pass z)."""

    def fn(lon, lat, z=None):
        return georef.lonlat_to_pixel(lon, lat)

    return fn


def polygons_to_rings(
    polygons, transform_fn, simplify_tolerance: float
) -> list[list[list[int]]]:
    rings = []
    for poly in polygons:
        projected = shapely_transform(transform_fn, poly)
        if simplify_tolerance > 0:
            projected = projected.simplify(simplify_tolerance, preserve_topology=True)
        if projected.is_empty:
            continue
        exterior = list(projected.exterior.coords)[:-1]  # drop closing duplicate
        ring = []
        for x, y in exterior:
            point = [round(x), round(y)]
            if not ring or point != ring[-1]:  # rounding can collapse close points
                ring.append(point)
        if ring and ring[0] == ring[-1]:
            ring.pop()
        if len(ring) < 3:
            continue
        rings.append(ring)
    return rings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"),
        help="Lon/lat calibration box. If given, it is persisted into "
        "map.json's georef block (the single source of truth every geo "
        "layer reads). If omitted, the existing georef is reused.",
    )
    parser.add_argument(
        "--project", type=Path, default=Path("dev_map_data/project.json")
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=Path("dev_map_data/map.json"),
        help="Manifest to read the pixel canvas size from (ignored if --size given).",
    )
    parser.add_argument(
        "--size",
        nargs=2,
        type=int,
        metavar=("WIDTH", "HEIGHT"),
        help="Pixel canvas size, overriding --map. Needed when map.json doesn't "
        "exist yet, e.g. generating a backdrop for init_package.py.",
    )
    parser.add_argument(
        "--backdrop-out",
        type=Path,
        help="Also render a two-tone land/sea silhouette PNG at this path, "
        "suitable as input to init_package.py --backdrop.",
    )
    parser.add_argument(
        "--skip-project",
        action="store_true",
        help="Don't read/write project.json -- use with --backdrop-out alone.",
    )
    parser.add_argument("--layer", default="coastline")
    parser.add_argument("--key", default="land")
    parser.add_argument(
        "--simplify",
        type=float,
        default=0.02,
        help="Simplification tolerance in degrees (0 to disable).",
    )
    parser.add_argument("--url", default=NE_LAND_URL)
    args = parser.parse_args()

    manifest = None
    if args.map.is_file() and not args.size:
        manifest = json.loads(args.map.read_text())
        size = tuple(manifest["size"])
    elif args.size:
        size = tuple(args.size)
    else:
        raise SystemExit(f"no {args.map} to read canvas size from; pass --size")

    georef = resolve_georef(args.bbox, manifest, size)

    # Persist the calibration so every geo layer shares it (unless we're
    # working off a bare --size with no map.json to write to).
    if args.bbox is not None and manifest is not None:
        manifest["georef"] = georef.to_manifest_block()
        args.map.write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"Wrote georef {list(georef.bbox)} to {args.map}")

    shp_path = fetch_shapefile(args.url)
    polygons = load_land_polygons(shp_path, georef.bbox)
    rings = polygons_to_rings(polygons, shapely_transform_fn(georef), args.simplify)

    if not rings:
        raise SystemExit("No land polygons found in that bbox -- check --bbox order.")

    if args.backdrop_out:
        img = Image.new("RGB", size, SEA_COLOR)
        draw = ImageDraw.Draw(img)
        for ring in rings:
            draw.polygon([tuple(p) for p in ring], fill=LAND_COLOR)
        args.backdrop_out.parent.mkdir(parents=True, exist_ok=True)
        img.save(args.backdrop_out)
        print(f"Wrote backdrop silhouette to {args.backdrop_out}")

    if args.skip_project:
        return

    project = json.loads(args.project.read_text())
    layer = project.setdefault("layers", {}).setdefault(args.layer, {"features": []})
    layer["features"] = [
        f for f in layer.get("features", []) if f.get("key") != args.key
    ] + [{"key": args.key, "polygons": rings}]
    args.project.write_text(json.dumps(project, indent=1))

    print(f"Wrote {len(rings)} ring(s) to {args.layer}.{args.key} in {args.project}")
    print("Run `make map-editor-preview` to check it, then touch up in the editor.")


if __name__ == "__main__":
    main()
