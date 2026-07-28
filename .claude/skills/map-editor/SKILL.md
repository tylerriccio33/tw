---
name: map-editor
description: How the campaign's territory-border map editor works (tools/map_editor) — tracing/editing faction region polygons, exporting, validating, previewing, and promoting to the live game. Use whenever asked to add/change/fix a faction's territory borders, region colors, or region_map.png/regions.txt, or to debug why the in-game province map looks wrong.
---

Reference for `tools/map_editor` — a browser-based tool for tracing faction territory borders on top of the campaign's painted terrain image. It is not part of the Godot game itself; it's an offline editing step that produces the two files `campaign/province_map.gd` actually loads at runtime.

## Mental model

- **Source of truth while editing:** `tools/map_editor/dev_map_data/project.json` — full-precision polygon points per region, in image pixel coordinates. This is what the editor UI reads and writes; treat it as the real editable data, not `region_map.png`.
- **Dev export:** `dev_map_data/region_map.png` (flat-colored raster, one solid color per territory) + `dev_map_data/regions.txt` (JSON map of `"#hexcolor": "Region_Name"`). Produced from `project.json` by the Export button. Never touches the live game.
- **Live game data:** `campaign/map_data/region_map.png` + `regions.txt` — same two-file format, loaded by `campaign/province_map.gd`. Only `make promote-map` copies the dev export here.
- **Backdrop:** `campaign/map_data/backdrop.png` — the painted terrain art everything is traced on top of, used for both the editor's background image and the land/sea classification heuristic.

Data flow: `project.json` (browser, full precision) --Export button--> `dev_map_data/{region_map.png,regions.txt}` --`make promote-map`--> `campaign/map_data/{region_map.png,regions.txt}` --Godot reimport--> live game.

## Running it

```
make map-editor        # launches http://localhost:8765
```

Or directly: `cd tools/map_editor && uv run server.py`. Flags: `--image` (backdrop to trace on, default `campaign/map_data/backdrop.png`), `--dev-dir`, `--game-dir`, `--port`.

On startup it loads, in priority order: existing `project.json` → re-traced from `dev_map_data`'s exported raster → re-traced from `campaign/map_data`'s live raster → an empty project. This is how you bootstrap an editing session from whatever's currently live.

## Using the editor UI

- Click to place polygon vertices; **Enter** or double-click closes the shape; **Backspace** undoes the last point; **Esc** cancels the in-progress shape.
- **WASD** pans, **Z/X** zoom out/in, Ctrl/Cmd+wheel also zooms.
- **+ New Region** creates a region (name + color), then starts its first polygon.
- **+ New Shape (island/exclave)** adds an additional disconnected polygon to the *currently selected* region (e.g. islands belonging to the same faction) — a region's `polygons` list in `project.json` can have more than one entry.
- **Edit Vertices** toggles drag-to-adjust mode on existing points instead of drawing new shapes.
- Points snap to nearby existing vertices/edges (other regions' borders) and to the traced coastline overlay (`/api/coastline`, cached in `dev_map_data/coastline.json`) — use this to get borders to actually meet the coastline instead of eyeballing it.
- **Reload From Live Game** re-imports from `campaign/map_data`'s current raster (discards the in-progress draft).
- **Save Draft** persists `project.json` without exporting rasters.
- **Export to Dev** runs the full export pipeline (see below) and writes `dev_map_data/{region_map.png,regions.txt}`.

## What Export actually does (`server.py:export_project`)

1. **`validate_project`** checks the whole project first and refuses to write anything if it finds: two different region names sharing the same hex color (regions.txt can only map one name per color — the other silently vanishes in-game), a polygon that revisits an exact point (pinches into a self-touching loop), a self-intersecting polygon (renders as a torn/disconnected fragment), or a point outside the image bounds. Errors are shown in the editor UI and block export entirely — see `tools/map_editor/gapfill.py`'s module docstring and `server.py`'s `validate_project`/`polygon_self_intersects` for the exact rules.
2. Each region's polygons are rasterized as flat, exact colors onto a white canvas.
3. **`gapfill.clip_sea_overflow`** then **`gapfill.fill_land_gaps`** reconcile the raster against `build_land_mask`'s land/sea classification of the backdrop: overshoot onto open water gets clipped back, and small gaps left by a border not quite reaching the coast get bridged (up to `MAX_GAP_PX`, currently 12px — tuned to the map's real tracing precision, not arbitrary). Both are conservative by design: an untraced country-sized patch of background is deliberately left alone rather than annexed into a neighboring region's color.
4. Writes `region_map.png` + `regions.txt`.

Colors must match exactly for `regions.txt` to recognize them — no blending/anti-aliasing; `province_map.gd` silently skips any raster color it doesn't have a name for.

## Fast iteration loop (no browser needed)

```
make map-editor-preview
```

Renders the current `dev_map_data/project.json` straight to `dev_map_data/preview.png`: region colors alpha-blended over the real backdrop (so misalignment with the actual coastline is obvious), running the same `validate_project` as a real export. If validation fails, the offending polygon(s) are outlined in red directly on the image and listed on stdout, with a non-zero exit code. Use this after every edit instead of re-opening the browser or promoting — read the PNG to review.

Direct form: `cd tools/map_editor && uv run preview.py [--project PATH] [--image PATH] [--out PATH]`.

## Promoting to the live game

```
make promote-map
```

Copies `dev_map_data/{region_map.png,regions.txt}` into `campaign/map_data/` and forces a Godot reimport (`godot --headless --import`) — Godot caches the raster as a `.ctex` keyed by content hash, and `make play` never triggers a reimport on its own, so skipping this step leaves the game rendering a stale texture even though the source PNG changed. Then `make play` / `make play-shot` to see it in the actual game.

## Editing project.json directly

For programmatic/scripted edits (vs. clicking in the browser), `project.json` is:

```json
{
  "image_size": [1300, 647],
  "regions": [
    {"name": "Iberia", "color": "#ffe119", "polygons": [[[x, y], [x, y], ...], ...]}
  ]
}
```

Points are floats in backdrop pixel coordinates, one polygon ring per shape (no explicit closing point — the last point implicitly connects back to the first). After hand-editing this file, always run `make map-editor-preview` before promoting — it applies the same validation the editor UI does and will catch a bad edit (duplicate color, self-touching point, off-map point) before it reaches `campaign/map_data`.

## Tests

`make map-editor-test` (or `cd tools/map_editor && uv run --group dev pytest -q`) — covers `gapfill.py`'s fill/clip functions in isolation, `validate_project`'s invariants, the export/import raster round-trip, coastline classification/caching, and the HTTP API layer. `tests/test_gapfill_realistic.py` additionally regression-tests against the real `campaign/map_data/backdrop.png` and `dev_map_data/project.json` at full map scale/complexity — synthetic small-canvas tests alone have historically missed real-scale bugs here, so prefer extending that file over inventing another small synthetic fixture when testing gap-filling or validation against realistic geometry.
