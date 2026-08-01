# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Total War-style campaign map game built in Godot (GDScript), with the simulation core written in Rust and exposed as a GDExtension. There's also a standalone browser-based map editor (Python) used to author the map data the game loads.

## Architecture

Three layers, each with a distinct role:

1. **Rules/state — Rust (`rust/campaign/src/`)**: the entire simulation lives here. `model.rs` holds `Army`/`Campaign` state, movement, battle/siege resolution. `godot_api.rs` is the GDExtension boundary: `#[func]` bindings GDScript calls, `get_state()` snapshot dict, signal emission (`army_moved`, `army_battle`, `game_over`). GDScript never computes game rules — it only calls into Rust and renders the result.
2. **Rendering/input — GDScript (`campaign/`)**: `campaign_ui.gd` is the scene owner (loads the map package, wires camera input, builds the HUD, refreshes on every signal). `army_layer.gd` (2D markers/input) and `army_models.gd` (3D knight models, purely visual) both read the same Rust state snapshot independently — the 2D layer tweens a lagging "currently drawn" position that the 3D layer then mirrors, so the knight never teleports even though the model does. `province_map.gd`/`region_area.gd` render provinces from `provinces.geo.json`; `map_package.gd` is the only reader of the map package files.
3. **Map authoring — Python (`tools/map_editor/`)**: a separate offline browser tool (not part of the Godot game) that produces everything under `campaign/map_data/`. A map is a *package of layers* — each layer is a raster + JSON legend. `map.json`'s layer order is simultaneously draw order, export order, and default snap order. Nothing in the pipeline hardcodes a layer name (enforced by `tests/test_extensibility.py`); adding a new layer type is data, not code.

Data flow for the map: `tools/map_editor` project.json + rasters → **Export** → `tools/map_editor/dev_map_data/` package → `make promote-map` → `campaign/map_data/` → Godot reimport → game.

Read the relevant skill (below) before working in any of these three areas — each has non-obvious call chains and gotchas that take longer to re-derive from source than to read once.

## Commands

Full command list and comments: `Makefile` / `make help`. Key ones:

- `make check` — parse every `.gd` file for errors (~1s, no render). Run this first when debugging anything GDScript.
- `make campaign` — build the Rust GDExtension (release) and install the dylib into `addons/campaign/bin/`. Re-run after any `rust/campaign/` change.
- `make campaign-test` — Rust unit tests (`cargo test`).
- `make campaign-smoke` — headless smoke test that the GDExtension loads.
- `make gut-test` — GDScript unit tests under `tests/unit/` (run `make gut` once per clone first to vendor the GUT addon).
- `make play` — build + open the campaign map in a window (`RESOLUTION=WxH` to override, default 1280x800).
- `make shot` — in-engine render of the campaign scene to `shots/play/shot.png`; prefer this over `play-shot` for reviewing visual changes (no OS permissions needed, can't come back black).
- `make play-shot` / `make hud-shot` — OS-level window screenshot / HUD crop; macOS only, needs Accessibility + Screen Recording permissions.
- `make render-test` — SSIM-gates a fresh capture against `tests/golden/`; `make render-test-update` accepts the current capture as the new baseline (only after confirming a diff was intentional).
- `make map-editor` — launch the browser map editor at http://localhost:8765 (writes to `tools/map_editor/dev_map_data/`, never touches the live map until promoted).
- `make map-editor-preview` — composite the layer stack to one PNG without the browser; run this after every map edit.
- `make map-editor-test` — map editor's pytest suite.
- `make map-package-check` — validate the dev map package without exporting (CI-able).
- `make promote-map` — copy the dev map package into `campaign/map_data/` and force a Godot reimport.

### Running a single test

- Rust: `cargo test --manifest-path rust/campaign/Cargo.toml <test_name>`
- Map editor (pytest): `cd tools/map_editor && uv run pytest -q <path::test_name>`
- GDScript (GUT): edit `.gutconfig.json`'s test dirs/files, or run `GODOT=godot ./tools/godot_gate.sh --headless -s res://addons/gut/gut_cmdln.gd -gtest=res://tests/unit/test_army_layer.gd -gexit`

## CI / pre-commit

**Run pre-commit after any non-trivial change** — use the `integrate` skill, or directly: `make ci MSG="<commit message>"`, which stages everything, runs `uvx prek run --all-files`, commits, and pushes to `origin main`. Keep fixing whatever pre-commit flags and rerun until it's clean.

`.pre-commit-config.yaml` hooks (by file type):
- Python: `ruff-check --fix`, `ruff-format`, `pyrefly check tools`, map-editor pytest suite.
- GDScript: `gdformat`, `gdlint` (via `gdtoolkit`).
- Rust (`rust/campaign/*.rs`): `cargo fmt`, `cargo clippy -- -D warnings`, `cargo test`.
- Any `campaign/*.gd` or `tests/*.gd` change: `make gut-test`.
- Any change touching campaign visuals (`.gd`/`.gdshader`/`.tscn`, `map_data`, `assets/armies|buildings`, `shoot.gd`/`render_test.py`, golden images): `make render-test`.
- General: trailing whitespace, EOF fixer, merge-conflict markers, large files, YAML/JSON/TOML syntax, shellcheck, `slop-lint`, private-key detection.

## Skills

Project skills live in `.claude/skills/` — invoke with `/skill-name` or via the Agent tool's Skill mechanism when the task matches:

- **armies** — army state model, movement, battles/sieges, spawning, the 2D marker + 3D knight rendering split. Use for anything touching army behavior or `rust/campaign/`'s combat logic.
- **hud** — editing the campaign HUD (bottom banner, city panel, buttons). Most of the HUD is built procedurally in `campaign_ui.gd`, not in the `.tscn`.
- **map-editor** — the layered map package format and its editor: tracing, painting, exporting, validating, promoting.
- **visual-change-review** — workflow for making a visual change and confirming it rendered correctly (`make shot` → Read the PNG → `image-inspect` for measurable regressions).
- **image-inspect** — CV-based screenshot analysis (blur, borders/padding, SSIM diff, line alignment, dominant colors) instead of eyeballing images.
- **integrate** — run `make ci` with a commit message and iterate until it passes.

## Notes

- A fresh clone needs `make import` once (Godot only registers the GDExtension after a project import) and `make armies` / `make gut` once each to vendor assets/addons that are gitignored.
- `project.godot` has `treat_warnings_as_errors` on, so `make check` catches things like untyped inference failures that a normal Godot run would silently allow.
