# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this repo is

A Total War-style campaign game: **Python simulation + a Python-driven Unreal
5.8 frontend**. The defining constraint is that **the Unreal side has no C++
module** — every actor, material, light, and screenshot is built by the `tw`
Python package running inside the editor, driven by the `twctl` CLI. Agents drive
essentially the whole workflow through Unreal's editor Python API.

| Path | Role |
| --- | --- |
| `sim/` (`tw_sim`) | Python simulation — the source of truth for all rules. Untouched by the frontend. |
| `unreal/Content/Python/tw/` | The frontend: builds and drives the campaign map through the `unreal` Python API. |
| `tools/twctl/` | The agent-facing CLI. Launches the editor headlessly or drives a live one over remote execution. |
| `bake/` (`tw-bake`) | One-shot Rust geometry baker → `unreal/Content/Map/` (offline only). |

The north star for the look is `target-state.png`. Current slice is **visuals
first**: match the reference on fixed-camera golden shots with read-only province
borders and settlement markers; full click-to-command interactivity is slice 2
(stubbed in `tw/campaign.py`).

## Commands (`make help` lists all)

- `make py-test` — the Python simulation test suite. **This is the gate.**
- `make bridge-test` — **reach for this first** when touching the sim bridge. Runs
  the pure-Python `tw.simbridge` (and its vendored msgpack codec) against a real
  `tw_sim` sidecar in ~2s, no editor. Plays the role the old `cpp-test` did.
- `make bake` — regenerate `unreal/Content/Map/` (heightmap, borders, rivers,
  forests, provinces). Only needed when geometry/elevation code changes.
- `make build` — headless: build + save the whole world from the bake + a snapshot.
- `make shot [SHOTS="mountain border"]` — **the visual loop.** Render fixed-camera
  presets to `unreal/Shots/current/`; `make shots-diff` vs `golden/`, `make
  shots-bless` to accept. The PNGs are how a visual change is *seen*; two identical
  runs diff to ~0, so any number is a real change.
- `make live` + `make exec CODE=...` — **the tight loop.** One persistent editor
  with Python remote-execution on; push a snippet (e.g. rebuild a material) into it
  from another shell instead of paying a relaunch.
- `make sim` — run the sidecar standalone (writes `sim/.sim-port`), so a live
  editor attaches to it; restart Python without closing the editor.
- `make assets` — headless: (re)build the code-owned materials.

Override the engine with `TW_UE=<dir>` (default `/Users/Shared/Epic Games/UE_5.8`).

**Disk is the binding constraint on this machine.** A full editor launch has
twice filled the volume until no shell command could run. `twctl` checks free
space before every launch and kills a headless run if free space drops below
~3 GB — do not defeat that guard.

## When an `unreal.*` Python call misbehaves

Check the docs and the engine source **before** iterating live against the
editor. A live-editor edit/run/read-log cycle is slow, and a wrong guess at a
UE Python API can hard-crash the process (`SIGSEGV`, not a catchable Python
exception) — burning a relaunch and disk headroom for no signal beyond "that
guess was wrong."

- Python API docs: `https://dev.epicgames.com/documentation/en-us/unreal-engine/python-api/class/<ClassName>` —
  fetch the exact class before guessing method/property names or signatures.
- The engine's C++ source is on disk at `$TW_UE/Engine/Source/` (e.g.
  `Runtime/Engine/Private/KismetRenderingLibrary.cpp`) and is authoritative —
  grepping it for a warning string or a function's guard conditions answers
  "why did this silently no-op" faster than any number of live retries. This
  is how the `-AllowCommandletRendering` requirement below was actually found.
- If you do end up crashing the editor, a `CrashReportClient -Unattended`
  process spawns and can spin indefinitely; find and `kill` it (`ps aux | grep
  -i crashreport`) rather than waiting for it to finish.

### UE 5.8 Python API gotchas already paid for (don't rediscover these)

- **Commandlets can't render by default.** `-run=pythonscript` gives you
  `FApp::CanEverRender() == false` unless the launch also passes
  `-AllowCommandletRendering` (already in `twctl`'s `_run_editor_headless`).
  Without it, anything GPU-backed — render targets, scene captures — silently
  no-ops (`UKismetRenderingLibrary::ExportRenderTarget` logs "render target
  has been released" and writes nothing; no Python exception).
  `unreal.AutomationLibrary.take_high_res_screenshot` is a harder case: it
  reaches into the (nonexistent) level-editor viewport client and hard-crashes
  the process even with that flag — use a spawned `unreal.SceneCapture2D`
  actor + an asset-backed `unreal.TextureRenderTarget2D` (**not** the
  transient `RenderingLibrary.create_render_target2d`, which is unrooted and
  can be GC'd between capture and export) +
  `RenderingLibrary.export_render_target` instead. That function does not
  append a file extension — pass the full filename, not the stem.
- **`ALandscape` placement crashes outside an interactive editor.**
  `spawn_actor_from_class(unreal.Landscape, ...)` goes through UE's
  actor-placement factory, which touches `GLevelEditorModeTools()` — a fatal
  assert in a commandlet. Detect commandlet mode
  (`"-run=pythonscript" in unreal.SystemLibrary.get_command_line()`) and skip
  a fatal assert in a commandlet. Since `build`/`shot` are commandlets, the
  landscape path could never run in the loop that produces the goldens, so
  `landscape.py` has no landscape path at all — terrain is always the baked OBJ
  as a Nanite static mesh.
- **The Interchange OBJ translator requires a UV channel.** A `f v//vn` OBJ with
  no `vt` lines is legal OBJ but trips a *handled ensure*
  (`UVs.IsValidIndex(VertexData.UVIndex)`) — logged, no Python exception, no
  asset produced. The baker emits `vt` per vertex for exactly this reason; if
  terrain ever vanishes from the level, grep the log for that ensure first.
- **`unreal.Rotator`'s positional order is `(roll, pitch, yaw)`**, not the
  `(pitch, yaw, roll)` that `Shot.rotation` and most UE UI use. Always construct
  it with keywords; passing a pitch-first tuple straight through silently rolls
  the camera instead of tilting it.
- **Component/property names often aren't what the class name suggests:**
  `ADirectionalLight`/`ASkyLight` both expose `.light_component` (not
  `.directional_light_component`/`.sky_light_component`);
  `AExponentialHeightFog` exposes `.component` as a property, not
  `.get_component()`, and its inscattering color needs the dedicated
  `set_fog_inscattering_color()` setter, not `set_editor_property(...)`.
- **`HitResult` struct fields are unreadable directly** (`get_editor_property`
  included) — call `.to_dict()` and index into that.
- **No static `MaterialInstanceDynamic.create`.** Create dynamic material
  instances via the `PrimitiveComponent` instance method
  `create_and_set_material_instance_dynamic_from_material(...)`.

## Architecture

```
  twctl (tools/) ──▶ UnrealEditor(-Cmd) ── embedded Python (unreal) ──┐
                        tw/ package:                                   │
                        world → landscape/materials/water/forests/     │
                                borders/markers/lighting/render        │
                        reads unreal/Content/Map/ (bake outputs)       │
                        ── socket(msgpack) ──▶ tw_sim.server ──▶ api.Simulation
  bake/ (Rust) ── make bake ──▶ unreal/Content/Map/*.{r16,json}
```

The hard rule, unchanged from the old design: **the frontend contains no game
rules.** Every player action becomes a command; the visible layer is rebuilt from
the snapshot. The AI is not special-cased — it issues ordinary commands through
the same `rules.apply` path.

### `unreal/Content/Python/tw/`

- `simbridge.py` — the client half of the sim bridge, **pure Python, no deps.**
  4-byte length prefix + msgpack over loopback TCP (see `sim/server.py`). UE ships
  no `msgpack`, so a minimal codec is vendored here — the thing that makes
  `bridge-test` possible. Nothing calls the transport per frame.
- `world.py` — `build_world()`, the one orchestration entry point. Materials →
  terrain → static geography → ownership layers → lighting. **Requires a live
  sidecar** — `make sim` (or `twctl sim`) before `make build`/`make shot`.
- `landscape.py` — `terrain.obj` → a Nanite static mesh, the single terrain path.
  The terrain material reads world-Z, so it needs no painted weightmaps.

**No fallbacks anywhere in this package.** A missing asset, a failed import, an
unreachable sim, or a shot that wrote no PNG raises. This is deliberate and was
paid for: a silent OBJ-import failure plus a neutral-snapshot fallback once
produced five all-black golden shots and a `build_world` that logged a clean
"done". If you are tempted to add a `try`/`except` that degrades, don't.
- `materials/` — assets-as-code. Material graphs built via
  `unreal.MaterialEditingLibrary` and saved under `/Game/Generated/Materials`, so a
  look change is a reviewable diff, never hand-authored. `terrain.py` bands on the
  *actual* baked height range (see the EXAG note below).
- `water.py`, `forests.py`, `borders.py`, `markers.py`, `lighting.py` — the layers.
  Static geography is built once; only ownership layers (borders, markers) rebuild
  on a new snapshot (`campaign.apply_snapshot`).
- `render.py` + `presets.py` — fixed-camera preset screenshots to `Shots/current/`.
- `_scene.py` — everything spawned is tagged `tw` + a layer tag, so a rebuild is
  diff-free by construction (`clear(layer)` removes exactly the last run's actors).
- `entry/*.py` — the headless entry points `twctl` runs under `-run=pythonscript`;
  they read parameters from env vars (`TW_CAMPAIGN`, `TW_SEED`, `TW_SHOTS`).

### `bake/` and the EXAG coupling

`make bake` runs the Rust generators once and writes `unreal/Content/Map/`:
**`terrain.obj`** (the terrain mesh), `heightmap.r16` + `terrain_meta.json`,
`province_borders.json`, `rivers.json`, `forests.json`, `provinces.json`. Output is
gitignored — regenerate freely. Coordinate conversion (Godot → Unreal cm) happens
**only** in the baker.

Elevation is exaggerated (`bake/src/geom/terrain.rs::EXAG`). Historically
every height/slope threshold in the terrain material was hand-calibrated against
that constant across the language boundary. Now `terrain_meta.json` carries the
height range and band anchors in cm, and `materials/terrain.py` bands as
*fractions of the actual baked range* — so the palette stays reachable even though
Britain peaks far below the European anchors. If the map goes uniformly green,
that coupling has drifted; check the range in `terrain_meta.json`.

## Workflow

  The core idea

  There's no C++ and no editor GUI clicking. You edit Python (in unreal/Content/Python/tw/) or
  config, and drive the editor from the terminal with twctl (via make). The editor is a renderer you
  push code into — headlessly for reproducible results, or into a live instance for speed.

  The tight visual loop (where you'll spend most time)

  This is the inner loop for "make it look like target-state.png":

  make live                        # window A: one persistent editor, remote-exec on
  # window B — edit tw/materials/terrain.py, then:
  make exec CODE='import importlib, tw.materials.terrain as t; importlib.reload(t); t.build()'
  make shot SHOTS=mountain         # render one preset, look at the PNG

  Edit → exec → look. A material tweak costs ~1–2s (a function re-run), not a 20s relaunch. The
  editor never restarts.

  The golden loop (locking a look in)

  When the change is right, make it reproducible and diffable:

  make shot            # render all presets → unreal/Shots/current/
  make shots-diff      # what moved vs golden/  (two identical runs diff to ~0)
  make shots-bless     # accept current/ as the new baseline

  Rule: a visual change without an updated golden is unfinished. The golden PNGs land in the PR
  alongside the code, so a reviewer sees the pixels change.

  Building the whole world (headless, reproducible)

  make bake && make build     # bake geometry, then build+save the full campaign map
  build runs entry/build.py under UnrealEditor-Cmd -run=pythonscript — cold, deterministic, exits.
  This is the CI-shaped path; make shot even auto-builds if the level's empty.

  Changing game rules (unchanged from before)

  The sim is untouched territory:
  # edit sim/src/tw_sim/, add a test
  make py-test        # the gate
  make py-sim SEED=42 # watch an all-AI campaign for balance
  Only touch the frontend if a snapshot/command field crosses the wire — then edit tw/simbridge.py
  and run make bridge-test.

  The fast gate (before any commit)

  make bridge-test    # pure-Python bridge + msgpack codec vs a real sidecar, ~2s, no editor
  Pre-commit runs py-test, py-sim, bridge-test, clippy. Editor steps are excluded — too slow and
  disk-risky for a hook.

  Iterating on Python while the editor stays open

  make sim            # run the sidecar standalone (writes sim/.sim-port)
  A live editor's tw.simbridge attaches to it — restart Python without closing Unreal.

  ---
  The mental model: four loops at different speeds — geometry (bake, seconds, rare), visuals
  (live+exec, ~2s, constant), world assembly (build, ~minute, occasional), rules (py-test, seconds,
  separate). Everything an agent needs to do is a make target that ultimately runs tw-package Python
  inside the editor.

**Changing a game rule:** edit `sim/src/tw_sim/`, add a test, `make py-test`,
sanity-check with `make py-sim`. No frontend change unless a snapshot/command field
crosses the wire — then update `tw/simbridge.py` and verify with `make bridge-test`.

**Changing how it looks:** edit the material/lighting Python, then `make live` +
`make exec CODE='import importlib, tw.materials.terrain as t; importlib.reload(t);
t.build()'` to see it in the live editor, or `make shot` for the headless frame.
Look at the PNGs, `make shots-diff`, iterate, `make shots-bless` once it is right.
A visual change without an updated golden is unfinished.

**Changing geometry:** edit `bake/src/geom/*.rs`, `make bake`, `make build`.

## Pre-commit (prek)

`.pre-commit-config.yaml` runs `py-test`, `py-sim`, `bridge-test`, and
`cargo clippy` on every commit via `prek` (`uvx prek`). This is the local,
enforced substitute for CI — there is no GitHub Actions workflow. Editor-in-the-
loop steps (`make build`/`shot`) are deliberately excluded: too slow, and disk-
risky, for a commit hook.
