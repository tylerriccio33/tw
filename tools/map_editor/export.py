"""Turns an editing project into a map package the game can load.

One loop over manifest["layers"] does everything. For each layer it
rasterizes whatever its config describes, clips and gap-fills it against
earlier masks, then writes layers/<name>.png. A layer
that declares a `reduce` also collapses into a per-province tag.

Nothing here knows what "terrain" or "ownership" mean. They are just
layers whose configs happen to say `reduce.into: terrain` and
`reduce.into: starting_owner`.

The two derived outputs are the whole point.

  provinces.table.json  what each province *is* - neighbors, starting
                        owner, and every reduced tag. The simulation
                        reads this and never touches a pixel.
  provinces.geo.json    what each province *looks like* - polygon rings
                        traced from the final raster, after clip and
                        gapfill. Godot builds its Area2D geometry
                        straight from these, instead of re-deriving
                        polygons from a bitmap at load time.

Tracing rings from the exported raster, rather than copying the authored
polygons, is deliberate. Gapfill and clipping move borders. Geometry that
disagrees with the shipped raster is exactly the class of bug this
pipeline exists to remove.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import mapfmt
import numpy as np
from gapfill import fill_land_gaps
from PIL import Image, ImageDraw

# Contour tracing for provinces.geo.json. The raster is the source of
# truth, so simplification is deliberately tight - this is not the place
# to smooth a border, it's the place to describe one exactly.
GEO_SIMPLIFY_EPSILON = 0.75  # px, cv2.approxPolyDP tolerance
MIN_RING_AREA_PX = 8  # drops single-pixel specks left by gapfill

# Two provinces that merely touch at a corner aren't neighbors. A real
# shared border is many pixels long; diagonal contact is a tracing
# artifact.
MIN_SHARED_BORDER_PX = 8

# Overlap between two provinces below this is the 1px seam that snapped,
# genuinely-shared borders legitimately produce. Above it, someone drew
# the same land twice and the later feature silently ate the earlier one.
MAX_BENIGN_OVERLAP_PX = 64
MAX_BENIGN_OVERLAP_FRACTION = 0.01


def _save_with_nodata_transparent(
    raster: np.ndarray, cfg: mapfmt.LayerConfig, path: Path
) -> None:
    """Write a layer raster with its nodata pixels fully transparent.

    "Nothing painted here" has to render as nothing. Flat RGB would let a
    terrain layer black out the ocean the moment Godot mounts it as a
    sprite. Every layer would hide the ones beneath it.

    This keeps the color channels untouched, including under transparent
    pixels. Reading the file back with `convert("RGB")` then returns
    exactly the bytes it wrote. The brush round-trip and every
    exact-color match downstream depend on that.
    """
    nodata = np.array(cfg.nodata_rgb, dtype=np.uint8)
    alpha = np.where(np.all(raster == nodata, axis=-1), 0, 255).astype(np.uint8)
    rgba = np.dstack([raster, alpha])
    Image.fromarray(rgba, mode="RGBA").save(path)


class ExportBlocked(ValueError):
    """Raised before export writes anything. A half-finished export leaves
    the package internally inconsistent - a raster describing one map, a
    table describing another."""


# --------------------------------------------------------------------------
# rasterizing
# --------------------------------------------------------------------------


def _blank(size: tuple[int, int], rgb: tuple[int, int, int]) -> np.ndarray:
    w, h = size
    canvas = np.empty((h, w, 3), dtype=np.uint8)
    canvas[:] = np.array(rgb, dtype=np.uint8)
    return canvas


def _draw_polygons(
    canvas: np.ndarray, polygons, rgb: tuple[int, int, int]
) -> np.ndarray:
    """PIL's polygon fill, not cv2's, so this matches what the editor's
    SVG preview and preview.py draw."""
    image = Image.fromarray(canvas)
    draw = ImageDraw.Draw(image)
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        draw.polygon([(float(x), float(y)) for x, y in polygon], fill=rgb)
    return np.array(image)


def rasterize_polygon_layer(
    project: dict, cfg: mapfmt.LayerConfig, size: tuple[int, int]
) -> np.ndarray:
    canvas = _blank(size, cfg.nodata_rgb)
    for feature in mapfmt.project_features(project, cfg.name):
        if cfg.kind == "identity":
            rgb = mapfmt.id_to_color(int(feature["id"]))
        else:
            rgb = mapfmt.hex_to_rgb(cfg.color_for_key(feature["key"]))
        canvas = _draw_polygons(canvas, feature.get("polygons", []), rgb)
    return canvas


def rasterize_brush_layer(
    package: mapfmt.Package, cfg: mapfmt.LayerConfig, size: tuple[int, int]
) -> np.ndarray:
    """A brush layer's PNG *is* its source - the editor paints into it
    directly and posts it back. Export only re-reads it, to clip and
    gap-fill it like everything else."""
    path = package.raster_path(cfg.name)
    if not path.is_file():
        return _blank(size, cfg.nodata_rgb)
    with Image.open(path) as im:
        raster = np.array(im.convert("RGB"))
    if (raster.shape[1], raster.shape[0]) != size:
        raise ExportBlocked(
            f"layer '{cfg.name}' raster is "
            f"{raster.shape[1]}x{raster.shape[0]} but the map is "
            f"{size[0]}x{size[1]} - repaint it or fix map.json's size"
        )
    return raster


def rasterize_assign_layer(
    project: dict,
    cfg: mapfmt.LayerConfig,
    size: tuple[int, int],
    id_buf: np.ndarray | None,
) -> np.ndarray:
    """Paint each province solid with whatever key it carries.

    Nothing at runtime reads this PNG. Ownership reaches the game through
    provinces.table.json's starting_owner, and after turn one the
    simulation owns it outright. The raster exists so the layer stack
    composites into a legible preview like every other layer.
    """
    canvas = _blank(size, cfg.nodata_rgb)
    if id_buf is None:
        return canvas
    for raw_id, key in mapfmt.project_assignments(project, cfg.name).items():
        rgb = mapfmt.hex_to_rgb(cfg.color_for_key(key))
        canvas[id_buf == int(raw_id)] = np.array(rgb, dtype=np.uint8)
    return canvas


POINT_RADIUS_PX = 4


def _owner_field_name(cfg: mapfmt.LayerConfig) -> str | None:
    for field_name, field_cfg in cfg.point_fields.items():
        if field_cfg.get("type") == "faction":
            return field_name
    return None


def rasterize_point_layer(
    project: dict,
    cfg: mapfmt.LayerConfig,
    size: tuple[int, int],
    id_buf: np.ndarray | None = None,
) -> np.ndarray:
    """Draw one filled dot per authored point.

    point_coupling == "province" (cities): the province's own identity
    color fills the dot directly. There is no legend to look up -
    the color comes straight from the province id.

    point_coupling == "free" (army starts, ...): the layer's legend
    supplies the color, keyed by the point's "faction" payload field.
    A brush/class layer colors a key the same way.
    """
    canvas = _blank(size, cfg.nodata_rgb)
    image = Image.fromarray(canvas)
    draw = ImageDraw.Draw(image)

    if cfg.point_coupling == "free":
        owner_field = _owner_field_name(cfg)
        for payload in mapfmt.project_points(project, cfg.name).values():
            x, y = payload["x"], payload["y"]
            owner = payload.get(owner_field) if owner_field else None
            rgb = (
                mapfmt.hex_to_rgb(cfg.color_for_key(owner)) if owner else cfg.nodata_rgb
            )
            draw.ellipse(
                [
                    x - POINT_RADIUS_PX,
                    y - POINT_RADIUS_PX,
                    x + POINT_RADIUS_PX,
                    y + POINT_RADIUS_PX,
                ],
                fill=rgb,
            )
        return np.array(image)

    for pid_str, (x, y) in mapfmt.project_points(project, cfg.name).items():
        rgb = mapfmt.id_to_color(int(pid_str))
        draw.ellipse(
            [
                x - POINT_RADIUS_PX,
                y - POINT_RADIUS_PX,
                x + POINT_RADIUS_PX,
                y + POINT_RADIUS_PX,
            ],
            fill=rgb,
        )
    return np.array(image)


def rasterize_layer(
    project: dict,
    package: mapfmt.Package,
    cfg: mapfmt.LayerConfig,
    size: tuple[int, int],
    id_buf: np.ndarray | None,
) -> np.ndarray:
    if cfg.input == "polygon":
        return rasterize_polygon_layer(project, cfg, size)
    if cfg.input == "brush":
        return rasterize_brush_layer(package, cfg, size)
    if cfg.input == "point":
        return rasterize_point_layer(project, cfg, size, id_buf)
    return rasterize_assign_layer(project, cfg, size, id_buf)


def id_buffer(raster: np.ndarray) -> np.ndarray:
    """Province raster -> (H, W) int32 of province ids. 0 is nodata."""
    r = raster[:, :, 0].astype(np.int32)
    g = raster[:, :, 1].astype(np.int32)
    b = raster[:, :, 2].astype(np.int32)
    return (r << 16) | (g << 8) | b


def key_buffer(
    raster: np.ndarray, cfg: mapfmt.LayerConfig
) -> tuple[np.ndarray, list[str]]:
    """Class raster -> (H, W) int32 of legend indices, 0 meaning no key.

    Also returns the key list those indices point into. Reducing over
    integer indices lets one bincount do every province at once."""
    keys = cfg.keys
    buf = np.zeros(raster.shape[:2], dtype=np.int32)
    for hex_color, entry in cfg.legend.items():
        rgb = np.array(mapfmt.hex_to_rgb(hex_color), dtype=np.uint8)
        buf[np.all(raster == rgb, axis=-1)] = keys.index(entry["key"]) + 1
    return buf, keys


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def _segments_properly_intersect(p1, p2, p3, p4) -> bool:
    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1 = orientation(p3, p4, p1)
    d2 = orientation(p3, p4, p2)
    d3 = orientation(p1, p2, p3)
    d4 = orientation(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0


def polygon_self_intersects(points) -> bool:
    """True if any two non-adjacent edges of the closed polygon cross.

    A self-crossing polygon fills unpredictably. PIL's scanline fill
    doesn't follow a torn/bowtie shape the way a human eye would. That
    is how a mistraced border renders as a disconnected fragment instead
    of the territory somebody meant."""
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent edges share a vertex, not a crossing
            if _segments_properly_intersect(*edges[i], *edges[j]):
                return True
    return False


def problem_polygons(
    project: dict, package: mapfmt.Package
) -> list[list[tuple[float, float]]]:
    """The same per-polygon geometry checks validate_package runs, but
    returning point lists instead of sentences. preview.py can then draw
    the offenders in red rather than only naming them."""
    width, height = package.size
    flagged = []
    for name in package.layer_order:
        if package.layers[name].input != "polygon":
            continue
        for feature in mapfmt.project_features(project, name):
            for polygon in feature.get("polygons", []):
                if len(polygon) < 3:
                    continue
                pts = [(float(x), float(y)) for x, y in polygon]
                out_of_bounds = any(
                    not (0 <= x <= width and 0 <= y <= height) for x, y in pts
                )
                torn = len(set(pts)) < len(pts) or polygon_self_intersects(pts)
                if out_of_bounds or torn:
                    flagged.append(pts)
    return flagged


def _validate_geometry(project: dict, package: mapfmt.Package) -> list[str]:
    width, height = package.size
    problems: list[str] = []

    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input != "polygon":
            continue
        for feature in mapfmt.project_features(project, name):
            label = feature.get("name") or feature.get("key") or "<unnamed>"
            for poly_idx, polygon in enumerate(feature.get("polygons", [])):
                if len(polygon) < 3:
                    continue
                pts = [(float(x), float(y)) for x, y in polygon]

                off_map = [
                    (x, y) for x, y in pts if not (0 <= x <= width and 0 <= y <= height)
                ]
                if off_map:
                    x, y = off_map[0]
                    problems.append(
                        f"{name}/'{label}' polygon {poly_idx} has a point "
                        f"({x:.1f}, {y:.1f}) outside the {width}x{height} map"
                    )

                if len(set(pts)) < len(pts):
                    problems.append(
                        f"{name}/'{label}' polygon {poly_idx} revisits the same "
                        "point twice - that pinches the shape into a "
                        "self-touching loop instead of the intended area"
                    )
                elif polygon_self_intersects(pts):
                    problems.append(
                        f"{name}/'{label}' polygon {poly_idx} crosses itself - it "
                        "will render as a torn or disconnected shape instead of "
                        "the intended area"
                    )
    return problems


def _validate_identity(project: dict, package: mapfmt.Package) -> list[str]:
    """Province ids and keys are how the sim, the save file and every
    config refer to a province. A duplicate means two different places
    answer to the same name."""
    problems: list[str] = []
    cfg = package.province_layer
    seen_ids: dict[int, str] = {}
    seen_keys: dict[str, str] = {}

    for feature in mapfmt.project_features(project, cfg.name):
        label = feature.get("name") or "<unnamed>"
        if "id" not in feature:
            problems.append(f"province '{label}' has no id")
            continue
        pid = int(feature["id"])
        if pid < 1:
            problems.append(
                f"province '{label}' has id {pid} - ids start at 1, since 0 "
                "encodes nodata in the province raster"
            )
        if pid in seen_ids and seen_ids[pid] != label:
            problems.append(
                f"provinces '{label}' and '{seen_ids[pid]}' both use id {pid}"
            )
        seen_ids[pid] = label

        key = feature.get("key") or mapfmt.slugify(label)
        if key in seen_keys and seen_keys[key] != label:
            problems.append(
                f"provinces '{label}' and '{seen_keys[key]}' both use key '{key}'"
            )
        seen_keys[key] = label

    return problems


def _validate_keys(project: dict, package: mapfmt.Package) -> list[str]:
    """Every key a feature or an assignment names has to exist in its own
    layer's legend. Otherwise export has no color to paint it with."""
    problems: list[str] = []
    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.kind == "identity":
            continue

        if cfg.input == "polygon":
            for feature in mapfmt.project_features(project, name):
                key = feature.get("key")
                if key not in cfg.keys:
                    problems.append(
                        f"{name} feature uses key '{key}', which isn't in that "
                        f"layer's legend ({', '.join(cfg.keys)})"
                    )

        if cfg.input == "assign":
            province_ids = {
                int(f["id"])
                for f in mapfmt.project_features(project, package.province_layer.name)
                if "id" in f
            }
            for raw_id, key in mapfmt.project_assignments(project, name).items():
                if int(raw_id) not in province_ids:
                    problems.append(
                        f"{name} assigns province {raw_id}, which doesn't exist"
                    )
                if key not in cfg.keys:
                    problems.append(
                        f"{name} assigns province {raw_id} the key '{key}', which "
                        f"isn't in that layer's legend ({', '.join(cfg.keys)})"
                    )
    return problems


def _validate_overlap(project: dict, package: mapfmt.Package) -> list[str]:
    """Two provinces claiming the same pixels. The raster holds one id per
    pixel, so the later-drawn province silently eats the earlier one. The
    map looks fine and the table is wrong."""
    problems: list[str] = []
    cfg = package.province_layer
    width, height = package.size
    claimed = np.zeros((height, width), dtype=np.int32)
    labels: dict[int, str] = {}

    for feature in mapfmt.project_features(project, cfg.name):
        if "id" not in feature:
            continue
        pid = int(feature["id"])
        labels[pid] = feature.get("name") or f"province {pid}"

        one = _draw_polygons(
            _blank((width, height), (0, 0, 0)),
            feature.get("polygons", []),
            (255, 255, 255),
        )
        mine = one[:, :, 0] > 0
        area = int(mine.sum())
        if area == 0:
            continue

        collision = claimed[mine]
        for other_id in np.unique(collision[collision != 0]):
            overlap = int((collision == other_id).sum())
            if overlap == area:
                # Wholly inside the other province and drawn after it -
                # that's an enclave, which is the one case where painting
                # over another province is exactly what was meant.
                continue
            tolerance = max(
                MAX_BENIGN_OVERLAP_PX, int(MAX_BENIGN_OVERLAP_FRACTION * area)
            )
            if overlap > tolerance:
                problems.append(
                    f"provinces '{labels[pid]}' and "
                    f"'{labels[int(other_id)]}' overlap by {overlap}px - the "
                    "raster holds one id per pixel, so whichever is drawn "
                    "later silently takes that land"
                )
        claimed[mine] = pid

    return problems


def _validate_points(project: dict, package: mapfmt.Package) -> list[str]:
    """A point-input layer couples 1:1 to the province layer. Each
    province needs exactly one authored point, on-map. Every authored
    point must reference a real province."""
    problems: list[str] = []
    width, height = package.size
    province_ids = {
        int(f["id"])
        for f in mapfmt.project_features(project, package.province_layer.name)
        if "id" in f
    }

    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input != "point" or cfg.point_coupling != "province":
            continue
        points = mapfmt.project_points(project, name)

        for pid_str, xy in points.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                problems.append(
                    f"{name} has a point keyed '{pid_str}', not a province id"
                )
                continue
            if pid not in province_ids:
                # prune_orphan_points drops these before this ever runs.
                continue
            if len(xy) != 2 or not (0 <= xy[0] <= width and 0 <= xy[1] <= height):
                problems.append(
                    f"{name} point for province {pid} at {xy} is outside the "
                    f"{width}x{height} map"
                )

        authored = {int(k) for k in points if k.lstrip("-").isdigit()}
        for pid in sorted(province_ids - authored):
            problems.append(f"{name} has no authored point for province {pid}")

    return problems


def _mask_bool(package: mapfmt.Package, project: dict, ref: str) -> np.ndarray:
    """Rasterize just the one layer a mask reference names, and return the
    boolean coverage for its key. Used by free-point validation, which
    runs before the main export loop has any rasters to reuse."""
    layer_name, key = ref.split(":", 1)
    cfg = package.layers[layer_name]
    raster = rasterize_layer(project, package, cfg, package.size, None)
    rgb = np.array(mapfmt.hex_to_rgb(cfg.color_for_key(key)), dtype=np.uint8)
    return np.all(raster == rgb, axis=-1)


def _validate_free_points(project: dict, package: mapfmt.Package) -> list[str]:
    """Free points (army starts, ...) aren't coupled to a province.
    Only the config itself gets checked: bounds and the declared
    payload schema. A layer that names a clip_to mask also requires
    the point to sit on it (e.g. "on land")."""
    problems: list[str] = []
    width, height = package.size
    faction_keys = set(package.faction_keys())

    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input != "point" or cfg.point_coupling != "free":
            continue
        points = mapfmt.project_points(project, name)

        mask = _mask_bool(package, project, cfg.clip_to) if cfg.clip_to else None

        for point_id, payload in points.items():
            if (
                not isinstance(payload, dict)
                or "x" not in payload
                or "y" not in payload
            ):
                problems.append(f"{name} point '{point_id}' has no x/y position")
                continue
            x, y = payload["x"], payload["y"]
            if not (0 <= x <= width and 0 <= y <= height):
                problems.append(
                    f"{name} point '{point_id}' at ({x}, {y}) is outside the "
                    f"{width}x{height} map"
                )
            elif mask is not None and not mask[int(y), int(x)]:
                problems.append(
                    f"{name} point '{point_id}' at ({x}, {y}) is not on '{cfg.clip_to}'"
                )

            for field_name, field_cfg in cfg.point_fields.items():
                value = payload.get(field_name)
                if field_cfg["type"] == "faction":
                    if value not in faction_keys:
                        problems.append(
                            f"{name} point '{point_id}' has {field_name}="
                            f"{value!r}, not a known faction"
                        )
                    elif value not in cfg.keys:
                        problems.append(
                            f"{name} point '{point_id}' has {field_name}="
                            f"{value!r}, which is a known faction but has no "
                            f"legend color in '{name}' - add it so the layer "
                            "can be rasterized"
                        )
                elif field_cfg["type"] == "counts":
                    keys = field_cfg["keys"]
                    minimum = field_cfg.get("min", 0)
                    if not isinstance(value, dict):
                        problems.append(
                            f"{name} point '{point_id}' has no {field_name} composition"
                        )
                        continue
                    for count_key in keys:
                        count = value.get(count_key, 0)
                        if not isinstance(count, int) or isinstance(count, bool):
                            problems.append(
                                f"{name} point '{point_id}' {field_name}."
                                f"{count_key}={count!r} isn't an integer"
                            )
                        elif count < minimum:
                            problems.append(
                                f"{name} point '{point_id}' {field_name}."
                                f"{count_key}={count} is below the minimum "
                                f"of {minimum}"
                            )
                    extra = set(value) - set(keys)
                    if extra:
                        problems.append(
                            f"{name} point '{point_id}' {field_name} has "
                            f"unknown key(s) {sorted(extra)}, expected one of "
                            f"{keys}"
                        )

    return problems


def prune_orphan_points(project: dict, package: mapfmt.Package) -> list[str]:
    """Drop any point-layer entry whose province id no longer exists.

    That province going away already means "delete its city" - there's
    nowhere reasonable to move the point to. Mutates project in place;
    returns what got dropped so the caller can tell the user.
    """
    province_ids = {
        int(f["id"])
        for f in mapfmt.project_features(project, package.province_layer.name)
        if "id" in f
    }
    dropped: list[str] = []
    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input != "point" or cfg.point_coupling != "province":
            continue
        points = mapfmt.project_points(project, name)
        for pid_str in list(points):
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            if pid not in province_ids:
                del points[pid_str]
                dropped.append(f"{name}: province {pid} no longer exists")
    return dropped


def validate_package(project: dict, package: mapfmt.Package) -> list[str]:
    """Everything that would make an export misrepresent what was drawn.
    export_package refuses to write anything until this is empty - a
    partially-written package is worse than no export at all."""
    return (
        _validate_geometry(project, package)
        + _validate_identity(project, package)
        + _validate_keys(project, package)
        + _validate_overlap(project, package)
        + _validate_points(project, package)
        + _validate_free_points(project, package)
    )


# --------------------------------------------------------------------------
# the derived tables
# --------------------------------------------------------------------------


def trace_rings(id_buf: np.ndarray, province_id: int) -> list[dict]:
    """Polygon rings for one province, holes included.

    RETR_CCOMP gives a two-level hierarchy: outer boundaries, and the
    holes inside them. RETR_EXTERNAL, which the earlier raster import
    used, throws the holes away and silently swallows enclaves.
    """
    mask = (id_buf == province_id).astype(np.uint8)
    contours, hierarchy = cv2.findContours(
        mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if hierarchy is None:
        return []

    rings = []
    for i, contour in enumerate(contours):
        if cv2.contourArea(contour) < MIN_RING_AREA_PX:
            continue
        simplified = cv2.approxPolyDP(contour, GEO_SIMPLIFY_EPSILON, True)
        if len(simplified) < 3:
            continue
        points: list[float] = []
        for pt in simplified:
            points.extend([float(pt[0][0]), float(pt[0][1])])
        rings.append({"points": points, "hole": int(hierarchy[0][i][3]) != -1})
    return rings


def compute_adjacency(id_buf: np.ndarray) -> dict[int, list[int]]:
    """Neighbors, from where different ids sit side by side.

    Only 4-connected contact counts, and only if the two provinces share
    at least MIN_SHARED_BORDER_PX of it. Corner-touching provinces are a
    tracing artifact, not a border an army could cross.
    """
    # Pack each unordered pair into one integer so a single np.unique
    # counts every shared border on the map at once.
    pairs: dict[tuple[int, int], int] = {}
    span = int(id_buf.max()) + 1
    for a, b in (
        (id_buf[:, :-1], id_buf[:, 1:]),
        (id_buf[:-1, :], id_buf[1:, :]),
    ):
        touching = (a != b) & (a != 0) & (b != 0)
        if not touching.any():
            continue
        lo = np.minimum(a[touching], b[touching]).astype(np.int64)
        hi = np.maximum(a[touching], b[touching]).astype(np.int64)
        flat, counts = np.unique(lo * span + hi, return_counts=True)
        for encoded, count in zip(flat.tolist(), counts.tolist()):
            key = (encoded // span, encoded % span)
            pairs[key] = pairs.get(key, 0) + count

    neighbors: dict[int, list[int]] = {}
    for (low, high), count in pairs.items():
        if count < MIN_SHARED_BORDER_PX:
            continue
        neighbors.setdefault(low, []).append(high)
        neighbors.setdefault(high, []).append(low)
    return {pid: sorted(ns) for pid, ns in neighbors.items()}


def reduce_tags(
    id_buf: np.ndarray,
    rasters: dict[str, np.ndarray],
    package: mapfmt.Package,
    areas: dict[int, int],
) -> dict[int, dict]:
    """Collapse every layer that declares a `reduce` into per-province
    tags. This is the step that lets the sim work in provinces while you
    author the map in pixels."""
    tags: dict[int, dict] = {pid: {} for pid in areas}

    for name in package.layer_order:
        cfg = package.layers[name]
        if not cfg.reduce:
            continue
        if cfg.input == "assign":
            # Already per-province in the project; reducing its raster
            # would just recover its own source, and land the same value
            # in tags as well as its own column.
            continue

        keys_buf, keys = key_buffer(rasters[name], cfg)
        tag_name = cfg.reduce["into"]
        mode = cfg.reduce["mode"]
        min_fraction = cfg.reduce.get("min_fraction", mapfmt.DEFAULT_MIN_FRACTION)

        # One bincount over (province, key) pairs covers the whole map.
        stride = len(keys) + 1
        inside = id_buf > 0
        combined = id_buf[inside].astype(np.int64) * stride + keys_buf[inside]
        counts = np.bincount(combined)

        for pid, area in areas.items():
            base = pid * stride
            per_key = {
                keys[i]: int(counts[base + i + 1])
                for i in range(len(keys))
                if base + i + 1 < len(counts) and counts[base + i + 1] > 0
            }
            if mode == "majority":
                # Nothing painted here falls back to the layer's declared
                # default rather than null, so the sim never has to ask
                # what a province with no terrain means.
                tags[pid][tag_name] = (
                    max(per_key.items(), key=lambda kv: kv[1])[0]
                    if per_key
                    else cfg.default_key
                )
            else:
                tags[pid][tag_name] = sorted(
                    key
                    for key, count in per_key.items()
                    if count >= min_fraction * area
                )

    return tags


def build_province_table(
    project: dict,
    package: mapfmt.Package,
    rasters: dict[str, np.ndarray],
    id_buf: np.ndarray,
) -> tuple[list[dict], list[dict]]:
    """(table rows, geo rows). Province id keys both, and the *final*
    raster produces both, so neither can disagree with it."""
    cfg = package.province_layer
    ids, counts = np.unique(id_buf[id_buf > 0], return_counts=True)
    areas = {int(pid): int(count) for pid, count in zip(ids.tolist(), counts.tolist())}

    neighbors = compute_adjacency(id_buf)
    tags = reduce_tags(id_buf, rasters, package, areas)

    # An `assign` layer is per-province by construction, so read it from
    # the project rather than reducing its raster back into what it came
    # from.
    assign_tags: dict[str, dict[int, str]] = {}
    for name in package.layer_order:
        layer = package.layers[name]
        if layer.input == "assign" and layer.reduce:
            assign_tags[layer.reduce["into"]] = {
                int(k): v for k, v in mapfmt.project_assignments(project, name).items()
            }

    city_layer = package.city_layer
    city_points = mapfmt.project_points(project, city_layer.name) if city_layer else {}

    table: list[dict] = []
    geo: list[dict] = []

    for feature in mapfmt.project_features(project, cfg.name):
        pid = int(feature["id"])
        if pid not in areas:
            continue  # nothing survived clipping; reported by validation

        ys, xs = np.nonzero(id_buf == pid)
        name = feature.get("name") or f"Province {pid}"
        row = {
            "id": pid,
            "key": feature.get("key") or mapfmt.slugify(name),
            "name": name,
            "centroid": [round(float(xs.mean()), 2), round(float(ys.mean()), 2)],
            "area_px": areas[pid],
            "neighbors": neighbors.get(pid, []),
            "tags": tags.get(pid, {}),
        }
        for tag_name, by_id in assign_tags.items():
            row[tag_name] = by_id.get(pid)
        if feature.get("color"):
            row["display_color"] = feature["color"]
        if city_layer is not None and str(pid) in city_points:
            row["city_position"] = list(city_points[str(pid)])

        table.append(row)
        geo.append({"id": pid, "rings": trace_rings(id_buf, pid)})

    return table, geo


def build_points_file(project: dict, package: mapfmt.Package) -> dict[str, list[dict]]:
    """Every point_coupling=free layer's authored points, keyed by layer
    name and sorted by id for a stable diff. Godot-side loading code
    would read this to spawn things at campaign start.

    An "army_starts" entry: {"id", "x", "y", "owner", "composition":
    {"archers", "melee", "cavalry"}}."""
    out: dict[str, list[dict]] = {}
    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input != "point" or cfg.point_coupling != "free":
            continue
        points = mapfmt.project_points(project, name)
        out[name] = [
            {"id": point_id, **payload} for point_id, payload in sorted(points.items())
        ]
    return out


# --------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------


def export_package(project: dict, package: mapfmt.Package) -> dict:
    size = package.size
    dropped_points = prune_orphan_points(project, package)
    if dropped_points:
        mapfmt.save_project(package.root, project)
    problems = validate_package(project, package)
    if problems:
        raise ExportBlocked(
            "Export blocked, the map data doesn't make sense:\n"
            + "\n".join(f"- {p}" for p in problems)
        )

    masks: dict[str, np.ndarray] = {}
    rasters: dict[str, np.ndarray] = {}
    id_buf: np.ndarray | None = None
    written: list[str] = []

    for name in package.layer_order:
        cfg = package.layers[name]
        raster = rasterize_layer(project, package, cfg, size, id_buf)

        if cfg.clip_to:
            # Somebody authored the mask; nothing guesses it from the
            # backdrop's brightness. Clipping is therefore exact, and
            # anything outside it sat over water, so it just goes.
            raster = raster.copy()
            raster[~masks[cfg.clip_to]] = np.array(cfg.nodata_rgb, dtype=np.uint8)

        if cfg.gapfill and cfg.input != "point":
            raster = fill_land_gaps(
                raster,
                masks[cfg.gapfill["within"]],
                cfg.nodata_rgb,
                cfg.gapfill.get("max_gap_px", 12),
            )

        if cfg.kind == "mask":
            for hex_color, entry in cfg.legend.items():
                rgb = np.array(mapfmt.hex_to_rgb(hex_color), dtype=np.uint8)
                masks[f"{name}:{entry['key']}"] = np.all(raster == rgb, axis=-1)

        if name == package.province_layer.name:
            id_buf = id_buffer(raster)

        rasters[name] = raster
        path = package.raster_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        _save_with_nodata_transparent(raster, cfg, path)
        written.append(str(path))

    if id_buf is None:
        raise ExportBlocked("no province layer was rasterized")

    survivors = set(np.unique(id_buf[id_buf > 0]).tolist())
    lost = [
        f.get("name") or f"province {f['id']}"
        for f in mapfmt.project_features(project, package.province_layer.name)
        if "id" in f and int(f["id"]) not in survivors
    ]
    if lost:
        raise ExportBlocked(
            "Export blocked, these provinces have no pixels left after "
            f"clipping to {package.province_layer.clip_to}: {', '.join(lost)}"
        )

    table, geo = build_province_table(project, package, rasters, id_buf)
    table_path = mapfmt.write_province_table(package.root, table)
    geo_path = mapfmt.write_province_geo(package.root, size, geo)
    points_by_layer = build_points_file(project, package)
    points_path = mapfmt.write_points_file(package.root, points_by_layer)

    return {
        "layers": written,
        "table": str(table_path),
        "geo": str(geo_path),
        "points": str(points_path),
        "province_count": len(table),
        "dropped_points": dropped_points,
    }


def export_dir(project: dict, package_dir: Path) -> dict:
    return export_package(project, mapfmt.load_package(Path(package_dir)))
