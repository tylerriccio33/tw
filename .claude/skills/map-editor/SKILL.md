---
name: map-editor
description: How the campaign's layered map package and its editor work (tools/map_editor) — tracing coastline/province polygons, painting terrain and resources, assigning starting owners, exporting, validating, previewing, and promoting to the live game. Use whenever asked to add/change/fix provinces, terrain, resources, faction ownership, the map format itself, or to debug why the in-game map looks wrong.
---

Reference for `tools/map_editor` — a browser-based tool for authoring the **map package** the campaign loads. It is not part of the Godot game; it's an offline editing step that produces everything in `campaign/map_data/`.

## Mental model

A map is a **package of layers**. A layer is a raster plus a JSON legend saying what its colors mean. That's the entire format.

```
campaign/map_data/
  map.json                  manifest: size, layer order, backdrop, province_layer
  factions.json             faction roster + colors
  backdrop.png              the line art everything is traced over
  layers/<name>.png         one raster per layer
  layers/<name>.json        that layer's legend and behaviour
  provinces.table.json      DERIVED - the simulation's input
  provinces.geo.json        DERIVED - Godot's polygon rings
```

`map.json`'s `layers` array is load-bearing in three ways at once: it is the **draw order**, the **export order**, and the **default snap order** (a layer may snap to any layer before it). Reordering it changes all three deliberately.

**Nothing in the pipeline knows a layer by name.** Adding roads/climate/culture is a PNG, a JSON legend, and one manifest entry — no code changes in Python, JS, GDScript or Rust. `tests/test_extensibility.py` enforces this; if you find yourself special-casing a layer name anywhere, that's the bug.

### The three authoring modes

A layer's `input` field picks its editing gesture:

| `input` | Layers | Source of truth | How you edit it |
|---|---|---|---|
| `polygon` | coastline, provinces | vector rings in `project.json` | trace with snapping + magnetic trace |
| `brush` | terrain, resources | **the layer PNG itself** | raster brush / bucket / eraser |
| `assign` | ownership | `assignments` map in `project.json` | click a province, pick a key |

`kind` says what the colors *mean*: `mask` (coastline — defines land vs sea), `identity` (provinces — each color is a province id), `class` (everything else — many pixels per key).

### Two ideas that are easy to get wrong

**Province colors are not political.** `layers/provinces.png` encodes province *ids* (`rgb24`: province 1 is `#000001`). It is machine-readable and looks black. A province's color in-game is whoever owns it **this turn**, pushed in by `province_map.apply_ownership()` from simulation state. Ownership changing hands changes no file on disk. `make map-editor-preview` renders the human-legible view.

**The coastline is authored data, not a heuristic.** `coastline.py`'s brightness classifier runs only to *seed* the coastline layer (`init_package.py`, or the editor's "Autotrace from backdrop"). After that the coastline layer is what every other layer clips and snaps to. Nothing re-guesses land from pixels at export.

### `reduce`: painted layers become province tags

The step that reconciles "layers of images" with "the sim wants per-region tags". A layer config's `reduce` is either:
- `{"into": "terrain", "mode": "majority"}` → the key covering the most pixels, falling back to the layer's `default_key` if nothing was painted
- `{"into": "resources", "mode": "any"}` → every key covering ≥2% of the province, as a sorted list

Results land in `provinces.table.json` under `tags`. Rust reads them as an untyped `HashMap<String, TagValue>` and never enumerates tag names.

## Running it

- `make map-package-init SEED=12` — create a fresh package from `backdrop.png`: manifest, layer configs, factions, a coastline traced from the line art, terrain seeded to plains. `SEED=N` also chops the land into N placeholder provinces so the game runs before anything is traced by hand. `FORCE=1` to overwrite configs.
- `make map-editor` — the editor at http://localhost:8765.
- `make map-editor-preview` — composite every layer over the backdrop → `dev_map_data/preview.png`. **Use this after every edit** instead of re-opening the browser. Runs the same validation as export; offending polygons get outlined in red on the image and listed on stdout with a non-zero exit.
- `make map-package-check` — validate without exporting. CI-able.
- `make promote-map` — copy the dev package into `campaign/map_data/` and force a Godot reimport. Godot caches each layer PNG as a `.ctex` keyed by content hash and `make play` never reimports on its own, so skipping this leaves the game rendering stale textures.

Data flow: `project.json` + brush rasters --Export--> `dev_map_data/` package --`make promote-map`--> `campaign/map_data/` --Godot reimport--> game.

## The editor UI

Sidebar is built entirely from `/api/manifest`. Layer list (radio = active, eye = visible, **⌁ = snap source**), then tools for the active layer's `input`, then its contents (provinces for `identity`, legend rows for everything else).

**Magnetic trace (`T`) is the ergonomic centerpiece.** Click once to lock onto a snapped boundary, move to see the stretch highlighted, click again to take it. It takes the *shorter* way round a closed ring; hold **Alt** for the long way. It re-anchors at the endpoint so traces chain along a coast. Implemented in `static/trace.js` — pure, no DOM, tested under node by `tests/test_trace.py`.

Other bindings: click places points, `Enter`/dbl-click closes a shape, `Backspace` undoes a point, `Esc` cancels, `WASD` pans, `Z/X` and Ctrl/Cmd+wheel zoom. In Edit Vertices, drag a handle to move it, click it to delete it, click a yellow midpoint to insert one.

**Fill Gaps** runs the export-time clip + gap-fill for one polygon layer and hands back *editable geometry*, so you can keep working on it and discard it by not saving. It also reports land too wide to bridge (> `max_gap_px`), which is the half that needs a real decision. Export runs clip + gap-fill unconditionally regardless.

## What Export does (`export.py:export_package`)

One loop over `manifest["layers"]`:

1. **`validate_package` first**, refusing to write anything if it finds: a self-intersecting or point-revisiting polygon, an off-map point, a duplicate province id/key, a feature naming a key outside its legend, two provinces overlapping by more than a 1px seam (full containment is allowed — that's an enclave), or an assignment referencing an unknown province/faction. A half-written package is worse than no export.
2. Rasterize the layer per its `input`.
3. `clip_to` — erase anything outside the named mask. Exact, because the mask is authored.
4. `gapfill` — hand unclaimed pixels inside the mask to their nearest neighbour, up to `max_gap_px`. Colors stay exact, never blended (`gapfill.py`).
5. Write `layers/<name>.png`. If `kind == mask`, publish its masks for later layers.
6. Then `build_province_table`: pixel-area centroids, adjacency (4-connected, ≥8 shared border px so corner-touching isn't a border), `reduce` tags, `starting_owner`.
7. Write `provinces.table.json` + `provinces.geo.json`.

Geometry is traced from the **final** raster (`RETR_CCOMP`, so enclaves stay holes), not copied from the authored polygons — gap-fill and clipping move borders, and shipping geometry that disagreed with the shipped raster is the exact class of bug this pipeline exists to remove.

Brush rasters posted from the browser are **quantized to exact legend colors** server-side (`server.py:quantize_to_legend`) — the canvas antialiases every stroke, and an off-legend pixel means nothing to any consumer.

## The game side

- `campaign/map_package.gd` — reads the package. Nothing else parses these files.
- `campaign/province_map.gd` — builds one `Area2D` per province straight from `provinces.geo.json`. No pixel scanning, no `BitMap.opaque_to_polygons`. Mounts `class` layers as sprites under the province fills. `apply_ownership()` recolors from sim state.
- `campaign/region_area.gd` — one province's click target; `set_owner_color()`, `clicked(province_id)`.
- `campaign/campaign_ui.gd` — `_ready()` loads the package, hands the table to Rust in **world space** (`_world_space_province_table`), and `_refresh()` calls `_apply_province_ownership()` every tick.
- `rust/campaign/src/godot_api.rs` — `load_factions()`, `load_provinces()`, `start_game_from_provinces()`. Note `variant_to_i64`: Godot's JSON has no integer type, so every number arrives as a float; accepting only i64 silently drops the whole table.

## Tests

`make map-editor-test` — format round-trips, the export pipeline (reduce modes, adjacency, clip/gap-fill, rgb24 ids, enclaves), the extensibility acceptance test, `quantize_to_legend`, coastline classification, and the trace extractor under node. `tests/test_realistic.py` runs the whole pipeline against the real `backdrop.png` and asserts the properties a playable map needs: no unclaimed land, nothing claiming sea, areas summing to the landmass, symmetric adjacency, a connected mainland, and geometry agreeing with its raster. Prefer extending that over inventing another small synthetic fixture — synthetic canvases have historically missed real-scale bugs here.

`make campaign-test` (Rust, incl. province ownership following city capture) and `make campaign-smoke` (asserts the table survives the FFI round trip, tags included, and that provinces actually change hands over a campaign).
