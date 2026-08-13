"""Bulk-add cities to a map from a lon/lat gazetteer CSV.

This turns "place lots of cities" from a visual, click-in-the-editor chore
into a data edit. The map's persisted georef (map.json, see geo.py)
projects each row's real lon/lat onto the pixel canvas. Cities thus land
on the coastline without anyone eyeballing pixels.

The source CSV needs at least name/lat/lng/population columns (the
simplemaps UK-cities basic set's schema: name,city_ascii,lat,lng,...,
population,...). Its license forbids redistribution, so nobody commits it.
Keep it under .geo_cache/ (gitignored) and point --csv at it. Only the
derived, curated points this script writes into project.json get committed
(after promote).

Idempotent and tunable. Every city this script adds gets an id prefixed
"ukc-". A re-run first drops all "ukc-*" points, then rebuilds from the CSV.
Changing --min-pop and re-running just replaces the managed set. Hand-placed
points (ids like "p1") stay put. A candidate that collides with an existing
city by name or position drops out rather than duplicating. Your hand-placed
Dublin/Caen/Rouen and any stylized spellings survive untouched.

Run one of these, then `make map-editor-preview`, then `make promote-map`.
    uv run add_cities.py --min-pop 30000     # default project/map/csv.
    uv run add_cities.py --min-pop 20000 --top 200     # cap the count.
    uv run add_cities.py --dry-run           # preview, write nothing.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path

import mapfmt
from geo import GeoRef

MANAGED_PREFIX = "ukc-"

DEFAULT_CSV = Path(".geo_cache/uk_cities.csv")
DEFAULT_PROJECT = Path("dev_map_data/project.json")
DEFAULT_MAP = Path("dev_map_data/map.json")

# Modern population -> game tier. Modern population is only a rough proxy for
# medieval weight, so treat this as a starting point and re-tier notable
# towns by hand (see docs/map_economy_plan.md). Descending so the first
# matching threshold wins.
TIER_THRESHOLDS = [
    (2_000_000, 5),
    (500_000, 4),
    (150_000, 3),
    (50_000, 2),
    (0, 1),
]


def tier_for_population(pop: float) -> int:
    for floor, tier in TIER_THRESHOLDS:
        if pop >= floor:
            return tier
    return 1


def normalize_name(name: str) -> str:
    """Fold accents, lowercase, strip non-alphanumerics -- so 'Bury St.
    Edmunds' and 'bury st edmunds' compare equal for dedup."""
    folded = unicodedata.normalize("NFKD", name)
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


class Candidate:
    __slots__ = ("lat", "lon", "name", "pop", "x", "y")

    def __init__(self, name: str, lat: float, lon: float, pop: float):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.pop = pop
        self.x = 0.0
        self.y = 0.0


def read_candidates(csv_path: Path, min_pop: float) -> list[Candidate]:
    """Parse the gazetteer, keep rows with a usable name/lat/lon and a
    population at or above the floor. Collapse duplicate names to the most
    populous row (the CSV has several 'Newcastle's)."""
    best: dict[str, Candidate] = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        name_col = cols.get("city") or cols.get("name")
        lat_col = cols.get("lat") or cols.get("latitude")
        lon_col = cols.get("lng") or cols.get("lon") or cols.get("longitude")
        pop_col = cols.get("population") or cols.get("pop_max")
        if not (name_col and lat_col and lon_col):
            raise SystemExit(
                f"{csv_path} needs name/lat/lng columns; got {reader.fieldnames}"
            )
        for row in reader:
            name = (row.get(name_col) or "").strip()
            try:
                lat = float(row[lat_col])
                lon = float(row[lon_col])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                pop = float(row.get(pop_col, "") or 0) if pop_col else 0.0
            except ValueError:
                pop = 0.0
            if not name or pop < min_pop:
                continue
            key = normalize_name(name)
            if key and (key not in best or pop > best[key].pop):
                best[key] = Candidate(name, lat, lon, pop)
    return list(best.values())


def snap_to_mask(
    points: dict[str, dict],
    mask,
    prefix: str,
    max_search: int,
) -> list[tuple[str, float]]:
    """Nudge every managed point that landed off `mask` (e.g. a port just
    outside the simplified coastline) to the nearest True pixel. Only the
    managed set moves; hand-placed points stay exactly where they are.
    Returns [(name, distance_moved)] for each nudged point."""
    import numpy as np

    h, w = mask.shape
    moved: list[tuple[str, float]] = []
    for pid, p in points.items():
        if not pid.startswith(prefix):
            continue
        x, y = round(p["x"]), round(p["y"])
        if 0 <= y < h and 0 <= x < w and mask[y, x]:
            continue
        # Expanding-window search for the closest on-mask pixel.
        for r in range(1, max_search + 1):
            y0, y1 = max(0, y - r), min(h, y + r + 1)
            x0, x1 = max(0, x - r), min(w, x + r + 1)
            window = mask[y0:y1, x0:x1]
            if not window.any():
                continue
            ys, xs = np.nonzero(window)
            gy, gx = ys + y0, xs + x0
            d2 = (gx - x) ** 2 + (gy - y) ** 2
            i = int(np.argmin(d2))
            p["x"], p["y"] = float(gx[i]), float(gy[i])
            moved.append((p.get("name", pid), float(np.sqrt(d2[i]))))
            break
    return moved


def load_layer_points(project: dict, layer: str) -> dict[str, dict]:
    return (
        project.setdefault("layers", {}).setdefault(layer, {}).setdefault("points", {})
    )


def merge(
    points: dict[str, dict],
    candidates: list[Candidate],
    georef: GeoRef,
    dedup_radius: float,
    top: int | None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Drop the managed set, then add candidates that don't collide with a
    kept (hand-placed) city or an already-added candidate. Returns
    (added names, [(candidate, reason-it-was-skipped)])."""
    # Keep only hand-placed points; the managed set is fully rebuilt.
    kept = {pid: p for pid, p in points.items() if not pid.startswith(MANAGED_PREFIX)}
    kept_by_name = {normalize_name(p.get("name", "")): p for p in kept.values()}
    # Growing list of (norm_name, x, y) we've committed to (kept + added).
    placed = [
        (normalize_name(p.get("name", "")), float(p["x"]), float(p["y"]))
        for p in kept.values()
    ]

    for c in candidates:
        c.x, c.y = georef.lonlat_to_pixel(c.lon, c.lat)

    # Most populous first: stable ids and the "important" city wins a
    # proximity tie over a smaller neighbour.
    candidates.sort(key=lambda c: (-c.pop, normalize_name(c.name)))
    if top is not None:
        candidates = candidates[:top]

    r2 = dedup_radius * dedup_radius
    added: list[str] = []
    skipped: list[tuple[str, str]] = []
    next_index = 1
    new_points: dict[str, dict] = {}

    for c in candidates:
        if not georef.contains_lonlat(c.lon, c.lat):
            skipped.append((c.name, "outside georef bbox"))
            continue
        key = normalize_name(c.name)
        if key in kept_by_name:
            skipped.append((c.name, "name matches an existing city"))
            continue
        collision = next(
            (nm for nm, px, py in placed if (c.x - px) ** 2 + (c.y - py) ** 2 <= r2),
            None,
        )
        if collision is not None:
            skipped.append((c.name, f"within {dedup_radius:.0f}px of '{collision}'"))
            continue
        pid = f"{MANAGED_PREFIX}{next_index}"
        next_index += 1
        new_points[pid] = {
            "x": c.x,
            "y": c.y,
            "name": c.name,
            "tier": tier_for_population(c.pop),
        }
        placed.append((key, c.x, c.y))
        added.append(c.name)

    points.clear()
    points.update(kept)
    points.update(new_points)
    return added, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument(
        "--layer", default=None, help="Point layer (default: map's city_layer)."
    )
    parser.add_argument(
        "--min-pop",
        type=float,
        default=30_000,
        help="Drop cities below this modern population (default 30000).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Keep at most this many, most populous first (after --min-pop).",
    )
    parser.add_argument(
        "--dedup-radius",
        type=float,
        default=100.0,
        help="Skip a candidate landing within this many pixels of an "
        "already-placed city (default 100 ~= 16km). Larger values collapse "
        "modern conurbations (London boroughs, Greater Manchester) down to "
        "their principal city, which suits a coarse campaign map.",
    )
    parser.add_argument(
        "--no-snap",
        action="store_true",
        help="Don't snap coastal cities onto the layer's clip_to land mask.",
    )
    parser.add_argument(
        "--snap-radius",
        type=int,
        default=150,
        help="Max pixels to search when snapping an off-land city (default 150).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print, write nothing.")
    args = parser.parse_args()

    if not args.csv.is_file():
        raise SystemExit(
            f"no gazetteer at {args.csv}. It is licensed data that isn't "
            "committed -- download the simplemaps UK-cities basic CSV and "
            f"save it there (see this script's docstring)."
        )

    manifest = json.loads(args.map.read_text())
    georef = GeoRef.from_manifest(manifest, tuple(manifest["size"]))

    project = json.loads(args.project.read_text())
    layer = args.layer or manifest.get("city_layer")
    if not layer:
        raise SystemExit("no --layer given and map.json has no city_layer")
    points = load_layer_points(project, layer)

    candidates = read_candidates(args.csv, args.min_pop)
    added, skipped = merge(points, candidates, georef, args.dedup_radius, args.top)

    # Coastal cities often land just off the simplified coastline. Snap them
    # onto the land mask; drop any that still can't reach land (rather than
    # leave an export-blocking off-land point).
    dropped: list[str] = []
    layer_cfg = None
    if not args.no_snap:
        package = mapfmt.load_package(args.project.parent)
        layer_cfg = package.layers[layer]
        if layer_cfg.clip_to:
            from export import mask_bool

            mask = mask_bool(package, project, layer_cfg.clip_to)
            snapped = snap_to_mask(points, mask, MANAGED_PREFIX, args.snap_radius)
            if snapped:
                print(f"snapped {len(snapped)} coastal cities onto land")
            h, w = mask.shape
            for pid in [p for p in points if p.startswith(MANAGED_PREFIX)]:
                x, y = round(points[pid]["x"]), round(points[pid]["y"])
                on = 0 <= y < h and 0 <= x < w and mask[y, x]
                if not on:
                    dropped.append(points[pid].get("name", pid))
                    del points[pid]
            if dropped:
                print(f"dropped {len(dropped)} not reachable to land: {dropped}")

    managed = sum(1 for pid in points if pid.startswith(MANAGED_PREFIX))
    manual = len(points) - managed
    print(
        f"{args.csv.name}: {len(candidates)} candidates (pop>={args.min_pop:.0f})"
        + (f", top {args.top}" if args.top else "")
    )
    print(f"added {len(added)} managed cities, kept {manual} hand-placed")
    print(f"skipped {len(skipped)} (name/proximity collisions)")
    near = [s for s in skipped if "px of" in s[1]]
    if near:
        print("near-collisions to eyeball (a stylized existing city?):")
        for name, reason in near[:25]:
            print(f"  {name}: {reason}")

    if args.dry_run:
        print("(dry run -- project.json not written)")
        return

    args.project.write_text(json.dumps(project, indent=1) + "\n")
    print(f"wrote {args.project} ({managed} managed + {manual} manual points)")
    print("next: `make map-editor-preview` to check, then `make promote-map`.")


if __name__ == "__main__":
    main()
