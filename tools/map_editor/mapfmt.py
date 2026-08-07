"""Reading and writing the layered map package format.

A map package is a directory. Everything in it is either *authored* or
*derived*. Authored means hand-drawn in the editor, or hand-edited JSON.
Derived means export.py wrote it and nobody touches it by hand.

    map.json                  manifest: size, layer order, backdrop.
    factions.json             faction roster + colors.
    backdrop.png              the art you trace on top of.
    layers/<name>.png         one raster per layer.
    layers/<name>.json        that layer's legend: what its colors mean.
    provinces.table.json      DERIVED - the simulation's input.
    provinces.geo.json        DERIVED - Godot's polygons.

The point of the format is that a layer is *only* a raster plus a legend.
Nothing in this module, in export.py, in the editor, or in the game knows
the name "terrain" or "resources". Another kind of map information takes
three things. A PNG, a JSON legend, and one entry in map.json's `layers`
list. See LayerConfig for what a legend declares.

manifest["layers"] is load-bearing in three separate ways. It is the draw
order, the export order, and the default snap order. A layer may snap to
any layer listed before it.

Reordering that list changes all three at once, on purpose. "Trace the
coastline, then terrain, then provinces, then assign owners" lives there
and nowhere else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "map.json"
LAYERS_DIRNAME = "layers"
TABLE_NAME = "provinces.table.json"
GEO_NAME = "provinces.geo.json"
POINTS_NAME = "points.json"
PROJECT_NAME = "project.json"

FORMAT_VERSION = 1
PROJECT_FORMAT_VERSION = 2

# Reserved in every layer raster. A province is never id 0, so a black
# pixel unambiguously means "nothing authored here" in any layer, which
# is what clip/gapfill and the reduce step both key off.
NODATA_COLOR = "#000000"

# How `reduce.mode == "any"` decides a key is actually present in a
# province rather than a few stray pixels bleeding over its border.
DEFAULT_MIN_FRACTION = 0.02

VALID_INPUTS = ("polygon", "brush", "assign", "point")
VALID_KINDS = ("mask", "identity", "class")
VALID_REDUCE_MODES = ("majority", "any")
VALID_POINT_COUPLINGS = ("province", "free")
VALID_POINT_FIELD_TYPES = ("faction", "counts", "tier", "name")


class PackageError(Exception):
    """A package is structurally unreadable - missing file, bad JSON, a
    layer config that doesn't declare what it is.

    validate_package() reports content problems instead: mistakes in what
    somebody *drew*. These are about a malformed package.
    """


# --------------------------------------------------------------------------
# colors
# --------------------------------------------------------------------------


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        raise PackageError(f"not a #rrggbb color: {hex_color!r}")
    try:
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise PackageError(f"not a #rrggbb color: {hex_color!r}") from exc


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(c) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def id_to_color(province_id: int) -> tuple[int, int, int]:
    """Encode a province id straight into its pixel value ("rgb24").

    Province 1 is #000001, province 258 is #000102. No lookup table, no
    palette to keep in sync, and collisions cannot happen. That is the
    whole reason the province raster isn't human-readable. Nobody
    eyeballs provinces.png; `make map-editor-preview` renders the
    legible composite instead.
    """
    if not 1 <= province_id <= 0xFFFFFF:
        raise PackageError(
            f"province id {province_id} out of range - ids are 1..16777215 "
            "(0 is reserved for nodata)"
        )
    return (
        (province_id >> 16) & 0xFF,
        (province_id >> 8) & 0xFF,
        province_id & 0xFF,
    )


def color_to_id(rgb) -> int:
    r, g, b = (int(c) for c in rgb)
    return (r << 16) | (g << 8) | b


def slugify(name: str) -> str:
    """'Egypt Levant' -> 'egypt_levant'.

    Province keys are the stable identity the sim and any hand-written
    config refer to. Retitling a display name must not change them.
    """
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    return s.strip("_")


# --------------------------------------------------------------------------
# layer configs
# --------------------------------------------------------------------------


@dataclass
class LayerConfig:
    """One layer's declaration.

    Everything the pipeline does to a layer comes from these fields.
    Nothing special-cases a layer by name.

    name        identifier, matches the manifest entry and filenames.
    title       what the editor's layer list shows.
    input       how you author it: polygon | brush | assign (see below).
    kind        what its colors mean: mask | identity | class.
    raster      filename under layers/.
    legend      hex -> {"key": ..., plus whatever else that layer cares
                about, e.g. "move_cost"}. Extra fields flow through to
                the province table untouched.
    clip_to     "<layer>:<key>" - pixels outside that mask get erased. For
                a point/free layer this also gates validation: an
                authored point must land inside that mask (e.g. "on
                land"), not just inside the canvas.
    gapfill     {"max_gap_px": N, "within": "<layer>:<key>"}.
    reduce      {"into": <tag name>, "mode": majority|any} or None.
    snap_source whether the editor offers it as a snap target by default.
    point_coupling  only meaningful when input == "point".
                "province" (default) - one point per province, keyed by
                province id, color is the province's identity color
                (see rasterize_point_layer). This is how cities work.
                "free" - any number of points, keyed by an arbitrary
                authored id, each carrying its own payload described by
                point_fields. Rasterizes in kind=class: the legend picks
                a color per payload field named by point_fields'
                "faction" entry (its own owner/faction). This is how
                army starting positions work.
    point_fields    only meaningful when point_coupling == "free". Maps
                payload field name -> {"type": "faction"|"counts"|"tier"
                |"name", ...}. "faction" means the value must be one of
                the package's faction keys. "counts" means the value is
                a dict of small-integer fields, e.g. {"keys": [...],
                "min": 0}. "tier" means the value is a small integer in
                [min, max] (default 1..5). A city's tier sets its
                growth speed; growth.py finds it by field type, not
                by name. "name" means a free-text label, shown next to
                the point on the map.
                Nothing here knows the word "army" or "city" - a
                "naval_starts" layer could reuse the exact same
                machinery.

    `input` is about the editing gesture, `kind` about the meaning.

      polygon/mask     coastline - traced rings, land against sea.
      polygon/identity provinces - traced rings, each one a distinct id.
      brush/class      terrain, resources - painted, many pixels a key.
      assign/class     ownership - nobody paints it; a province -> key
                       map, rasterized at export purely for preview.
      point/identity   province-coupled points (cities) - one per
                       province, color is the province id.
      point/class      free points (army starts, ...) - any number,
                       colored by their "faction" payload field.
    """

    name: str
    title: str
    input: str
    kind: str
    raster: str
    legend: dict[str, dict] = field(default_factory=dict)
    nodata_color: str = NODATA_COLOR
    default_key: str | None = None
    snap_source: bool = False
    clip_to: str | None = None
    gapfill: dict | None = None
    reduce: dict | None = None
    id_encoding: str | None = None
    point_coupling: str = "province"
    point_fields: dict[str, dict] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def nodata_rgb(self) -> tuple[int, int, int]:
        return hex_to_rgb(self.nodata_color)

    @property
    def keys(self) -> list[str]:
        return [entry["key"] for entry in self.legend.values()]

    def color_for_key(self, key: str) -> str:
        for hex_color, entry in self.legend.items():
            if entry["key"] == key:
                return hex_color
        raise PackageError(f"layer '{self.name}' has no legend entry for key {key!r}")

    def entry_for_key(self, key: str) -> dict:
        for entry in self.legend.values():
            if entry["key"] == key:
                return entry
        raise PackageError(f"layer '{self.name}' has no legend entry for key {key!r}")


def parse_layer_config(data: dict, name_hint: str = "") -> LayerConfig:
    name = data.get("name") or name_hint
    if not name:
        raise PackageError("layer config has no 'name'")

    input_mode = data.get("input")
    if input_mode not in VALID_INPUTS:
        raise PackageError(
            f"layer '{name}' has input={input_mode!r}, expected one of {VALID_INPUTS}"
        )

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        raise PackageError(
            f"layer '{name}' has kind={kind!r}, expected one of {VALID_KINDS}"
        )

    legend = {}
    for hex_color, entry in (data.get("legend") or {}).items():
        if "key" not in entry:
            raise PackageError(f"layer '{name}' legend entry {hex_color} has no 'key'")
        hex_to_rgb(hex_color)  # raises if malformed
        legend[hex_color.strip().lower()] = dict(entry)

    if kind == "identity" and legend:
        raise PackageError(
            f"layer '{name}' is kind=identity, so its colors are province ids "
            "(see id_to_color) - it must not also declare a legend"
        )
    if kind != "identity" and not legend:
        raise PackageError(
            f"layer '{name}' declares no legend, so its colors mean nothing"
        )

    reduce_cfg = data.get("reduce")
    if reduce_cfg is not None:
        if reduce_cfg.get("mode") not in VALID_REDUCE_MODES:
            raise PackageError(
                f"layer '{name}' has reduce.mode={reduce_cfg.get('mode')!r}, "
                f"expected one of {VALID_REDUCE_MODES}"
            )
        if not reduce_cfg.get("into"):
            raise PackageError(f"layer '{name}' has a reduce with no 'into' tag name")

    point_coupling = data.get("point_coupling") or "province"
    point_fields = dict(data.get("point_fields") or {})
    if input_mode == "point":
        if point_coupling not in VALID_POINT_COUPLINGS:
            raise PackageError(
                f"layer '{name}' has point_coupling={point_coupling!r}, "
                f"expected one of {VALID_POINT_COUPLINGS}"
            )
        if point_coupling == "province":
            if kind != "identity":
                raise PackageError(
                    f"layer '{name}' has point_coupling=province, so it must be "
                    f"kind=identity (province-coupled points aren't a legend), "
                    f"got kind={kind}"
                )
            if point_fields:
                raise PackageError(
                    f"layer '{name}' has point_coupling=province, which carries "
                    "no payload - point_fields belongs on a point_coupling=free layer"
                )
        else:  # free
            if kind != "class":
                raise PackageError(
                    f"layer '{name}' has point_coupling=free, so it must be "
                    f"kind=class (colored by its faction field), got kind={kind}"
                )
            for field_name, field_cfg in point_fields.items():
                field_type = field_cfg.get("type")
                if field_type not in VALID_POINT_FIELD_TYPES:
                    raise PackageError(
                        f"layer '{name}' point_fields.{field_name} has "
                        f"type={field_type!r}, expected one of "
                        f"{VALID_POINT_FIELD_TYPES}"
                    )
                if field_type == "counts" and not field_cfg.get("keys"):
                    raise PackageError(
                        f"layer '{name}' point_fields.{field_name} is type=counts "
                        "but declares no 'keys'"
                    )
                if field_type == "tier":
                    lo = field_cfg.get("min", 1)
                    hi = field_cfg.get("max", 5)
                    if not isinstance(lo, int) or not isinstance(hi, int) or lo > hi:
                        raise PackageError(
                            f"layer '{name}' point_fields.{field_name} is type=tier "
                            f"but has a nonsensical min/max ({lo}..{hi})"
                        )
    elif point_fields:
        raise PackageError(f"layer '{name}' has point_fields but input != 'point'")

    return LayerConfig(
        name=name,
        title=data.get("title") or name.replace("_", " ").title(),
        input=input_mode,
        kind=kind,
        raster=data.get("raster") or f"{name}.png",
        legend=legend,
        nodata_color=(data.get("nodata_color") or NODATA_COLOR).strip().lower(),
        default_key=data.get("default_key"),
        snap_source=bool(data.get("snap_source", False)),
        clip_to=data.get("clip_to"),
        gapfill=data.get("gapfill"),
        reduce=reduce_cfg,
        id_encoding=data.get("id_encoding"),
        point_coupling=point_coupling,
        point_fields=point_fields,
        raw=dict(data),
    )


# --------------------------------------------------------------------------
# the package
# --------------------------------------------------------------------------


@dataclass
class Package:
    """A loaded package directory. Holds only the authored declarations -
    the manifest, the layer configs, the faction roster. Callers load
    rasters and derived tables on demand, since export.py rewrites
    them."""

    root: Path
    manifest: dict
    layers: dict[str, LayerConfig]
    factions: list[dict]

    @property
    def size(self) -> tuple[int, int]:
        w, h = self.manifest["size"]
        return int(w), int(h)

    @property
    def layer_order(self) -> list[str]:
        return list(self.manifest["layers"])

    @property
    def province_layer(self) -> LayerConfig:
        return self.layers[self.manifest["province_layer"]]

    @property
    def city_layer(self) -> LayerConfig | None:
        name = self.manifest.get("city_layer")
        if not name:
            return None
        return self.layers[name]

    @property
    def backdrop_path(self) -> Path:
        return self.root / self.manifest.get("backdrop", "backdrop.png")

    @property
    def reference_path(self) -> Path | None:
        """An optional real-world image (e.g. Natural Earth shaded relief)
        shown in the editor as a toggleable visual reference. Purely a
        UI aid - not a layer, never exported or promoted, ignored by
        export.py and the game entirely."""
        name = self.manifest.get("reference")
        if not name:
            return None
        path = self.root / name
        return path if path.is_file() else None

    @property
    def layers_dir(self) -> Path:
        return self.root / LAYERS_DIRNAME

    def raster_path(self, layer_name: str) -> Path:
        return self.layers_dir / self.layers[layer_name].raster

    def faction_keys(self) -> list[str]:
        return [f["key"] for f in self.factions]

    def layers_before(self, layer_name: str) -> list[str]:
        """Every layer drawn before this one. That is the editor's default
        set of snap targets, and the only masks export can offer it."""
        order = self.layer_order
        return order[: order.index(layer_name)]


def load_package(root: Path) -> Package:
    root = Path(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackageError(f"no {MANIFEST_NAME} in {root} - not a map package")

    manifest = _read_json(manifest_path)
    for required in ("size", "layers", "province_layer"):
        if required not in manifest:
            raise PackageError(f"{manifest_path} has no '{required}'")

    layers: dict[str, LayerConfig] = {}
    for name in manifest["layers"]:
        cfg_path = root / LAYERS_DIRNAME / f"{name}.json"
        if not cfg_path.is_file():
            raise PackageError(
                f"{MANIFEST_NAME} lists layer '{name}' but {cfg_path} doesn't exist"
            )
        layers[name] = parse_layer_config(_read_json(cfg_path), name_hint=name)

    province_layer = manifest["province_layer"]
    if province_layer not in layers:
        raise PackageError(
            f"province_layer '{province_layer}' isn't one of the declared layers"
        )
    if layers[province_layer].kind != "identity":
        raise PackageError(
            f"province_layer '{province_layer}' must be kind=identity, "
            f"got kind={layers[province_layer].kind}"
        )

    factions_path = root / manifest.get("factions", "factions.json")
    factions = _read_json(factions_path) if factions_path.is_file() else []

    city_layer = manifest.get("city_layer")
    if city_layer:
        if city_layer not in layers:
            raise PackageError(
                f"city_layer '{city_layer}' isn't one of the declared layers"
            )
        cfg = layers[city_layer]
        if cfg.input != "point":
            raise PackageError(
                f"city_layer '{city_layer}' must be input=point, got input={cfg.input}"
            )
        if cfg.point_coupling == "province":
            if cfg.kind != "identity":
                raise PackageError(
                    f"city_layer '{city_layer}' has point_coupling=province, so it "
                    f"must be kind=identity, got kind={cfg.kind}"
                )
        else:  # free - cities seed province growth (see growth.py)
            if cfg.kind != "class":
                raise PackageError(
                    f"city_layer '{city_layer}' has point_coupling=free, so it "
                    f"must be kind=class, got kind={cfg.kind}"
                )
            if not any(fc.get("type") == "tier" for fc in cfg.point_fields.values()):
                raise PackageError(
                    f"city_layer '{city_layer}' has point_coupling=free, so it "
                    "needs a point_fields entry of type='tier' to drive province "
                    "growth speed"
                )

    _check_mask_references(layers, manifest["layers"])
    return Package(root=root, manifest=manifest, layers=layers, factions=factions)


def _check_mask_references(layers: dict[str, LayerConfig], order: list[str]) -> None:
    """A layer may only clip/gapfill against a mask from a layer drawn
    *before* it. Nothing later exists yet when its turn in the export loop
    comes up. Catching it here turns a confusing KeyError deep in export
    into a sentence naming both layers."""
    available: set[str] = set()
    for name in order:
        cfg = layers[name]
        for ref, field_name in (
            (cfg.clip_to, "clip_to"),
            ((cfg.gapfill or {}).get("within"), "gapfill.within"),
        ):
            if ref and ref not in available:
                raise PackageError(
                    f"layer '{name}' has {field_name}='{ref}', which isn't "
                    f"available yet - masks come from kind=mask layers listed "
                    f"earlier in {MANIFEST_NAME}. Available here: "
                    f"{sorted(available) or 'none'}"
                )
        if cfg.kind == "mask":
            for entry in cfg.legend.values():
                available.add(f"{name}:{entry['key']}")


def mask_refs(cfg: LayerConfig) -> list[str]:
    return [f"{cfg.name}:{entry['key']}" for entry in cfg.legend.values()]


# --------------------------------------------------------------------------
# derived files
# --------------------------------------------------------------------------


def write_province_table(root: Path, provinces: list[dict]) -> Path:
    path = Path(root) / TABLE_NAME
    _write_json(path, {"format_version": FORMAT_VERSION, "provinces": provinces})
    return path


def read_province_table(root: Path) -> list[dict]:
    return _read_json(Path(root) / TABLE_NAME)["provinces"]


def write_province_geo(root: Path, size: tuple[int, int], geo: list[dict]) -> Path:
    path = Path(root) / GEO_NAME
    _write_json(
        path,
        {"format_version": FORMAT_VERSION, "size": list(size), "provinces": geo},
    )
    return path


def read_province_geo(root: Path) -> dict:
    return _read_json(Path(root) / GEO_NAME)


def write_points_file(root: Path, points_by_layer: dict[str, list[dict]]) -> Path:
    """Derived file for every point_coupling=free layer's authored points,
    e.g. {"army_starts": [{"id": ..., "x": ..., "y": ..., "owner": ...,
    "composition": {...}}, ...]}. Nothing here knows the word "army".
    The key is just the layer's own name. A second free-point layer
    (say "naval_starts") shows up next to it with zero code changes."""
    path = Path(root) / POINTS_NAME
    _write_json(path, {"format_version": FORMAT_VERSION, "layers": points_by_layer})
    return path


def read_points_file(root: Path) -> dict[str, list[dict]]:
    return _read_json(Path(root) / POINTS_NAME)["layers"]


# --------------------------------------------------------------------------
# the editing project
# --------------------------------------------------------------------------


def empty_project(size: tuple[int, int], package: Package) -> dict:
    """A project with a slot for every layer the manifest declares.
    Brush layers get no geometry - their raster under layers/ is their
    source of truth, edited in place."""
    layers: dict[str, dict] = {}
    for name in package.layer_order:
        cfg = package.layers[name]
        if cfg.input == "polygon":
            layers[name] = {"features": []}
        elif cfg.input == "assign":
            layers[name] = {"assignments": {}}
        elif cfg.input == "point":
            layers[name] = {"points": {}}
        else:
            layers[name] = {}
    return {
        "format_version": PROJECT_FORMAT_VERSION,
        "size": list(size),
        "active_layer": package.layer_order[0] if package.layer_order else None,
        "layers": layers,
    }


def project_features(project: dict, layer_name: str) -> list[dict]:
    return project.get("layers", {}).get(layer_name, {}).get("features", []) or []


def project_assignments(project: dict, layer_name: str) -> dict[str, str]:
    return project.get("layers", {}).get(layer_name, {}).get("assignments", {}) or {}


def project_points(project: dict, layer_name: str) -> dict[str, list[float]]:
    return project.get("layers", {}).get(layer_name, {}).get("points", {}) or {}


def load_project(root: Path, package: Package) -> dict:
    path = Path(root) / PROJECT_NAME
    if not path.is_file():
        return empty_project(package.size, package)
    project = _read_json(path)
    version = project.get("format_version")
    if version != PROJECT_FORMAT_VERSION:
        raise PackageError(
            f"{path} is format_version {version}, this editor writes "
            f"{PROJECT_FORMAT_VERSION} - run `make map-editor-migrate`"
        )
    # A layer added to the manifest after this project was last saved
    # should just show up empty, not crash the editor on load.
    for name in package.layer_order:
        project.setdefault("layers", {}).setdefault(
            name, empty_project(package.size, package)["layers"][name]
        )
    return project


def save_project(root: Path, project: dict) -> Path:
    path = Path(root) / PROJECT_NAME
    _write_json(path, project)
    return path


# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError as exc:
        raise PackageError(f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f"{path} isn't valid JSON: {exc}") from exc


def _write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
