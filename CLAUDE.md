# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## What this repo is

A Total War-style campaign game: **Python simulation + Unreal C++ renderer**,
meeting at a narrow socket interface.

| Path | Role |
| --- | --- |
| `sim/` (`tw_sim`) | Python simulation — the source of truth for all rules |
| `unreal/` (`TotalWarlike`) | Unreal 5.8 C++ frontend |
| `bake/` (`tw-bake`) | One-shot Rust geometry baker → `unreal/Content/Map/` (offline only) |

The migration off Rust/Godot is **complete**. `engine/` (the original Rust
engine), `godot-client/` and `godot/` have been deleted; the geometry generators
the baker needs now live in `bake/src/geom/`, which is the only Rust left and the
only reason there is still a Cargo workspace.

## Commands

`make help` lists everything. The ones that matter day to day:

- `make py-test` — the Python test suite (`cd sim && uv run pytest`). This is the gate.
- `make py-sim SEED=42 ROUNDS=120` — headless all-AI campaign; fastest balance check.
- `make py-server` — run the sidecar standalone; writes `sim/.sim-port` so an
  already-open Unreal editor attaches to it instead of spawning its own. This is
  how you restart Python without closing the game.
- `make cpp-test` — **reach for this first** when touching the bridge. Compiles the
  engine-free half (`Sim/MsgPack.cpp`, `Map/ProvinceLookup.cpp`) with plain
  `clang++`, starts a real sidecar, exercises the whole protocol in ~2 s.
- `make unreal-build` / `make unreal-play` / `make unreal-test` — compile, play,
  and run the `TotalWarlike` automation tests — `.Sim` (slow confirmation of
  `cpp-test`) and `.Map` (the baked-map reader, incl. terrain winding).
  Override the engine with `UE=<dir>`; default `/Users/Shared/Epic Games/UE_5.8`.
- `make unreal-shots` — **the visual loop.** Renders five fixed-camera preset
  screenshots of the campaign map headlessly into `unreal/Shots/current/` in
  ~10s; `make shots-diff` compares them against the committed `golden/` set and
  `make shots-bless` accepts a new baseline. Two runs diff to exactly zero, so
  any number it prints is a real change. `make unreal-live` is the same thing
  interactively, with the console and shader hot-reload wired up for a ~2s
  edit/view loop on `TerrainCommon.ush`. See `unreal/Shots/README.md`.
- `make bake` — regenerate `unreal/Content/Map/`. Only needed when the coastline,
  elevation or derived-geometry code changes.

**Disk is the binding constraint on this machine.** A full editor launch has
twice filled the volume to the point that no shell command could run. Before any
editor run, check free space; run it in the background and kill it if free space
drops below ~3 GB.

## Pre-commit (prek)

`.pre-commit-config.yaml` runs `py-test`, `py-sim`, `cpp-test`, and
`cargo clippy` on every commit via [`prek`](https://github.com/j178/prek), the
Rust reimplementation of pre-commit — invoke it with `uvx prek`. This is the
local, enforced substitute for CI: there is no GitHub Actions workflow in this
repo, so these four checks are ~90% of what "correctness" means here.
`unreal-test` is deliberately excluded — a full editor build is too slow for a
commit hook — so it stays a manual step (see Workflow below).

- `make pre-commit-install` — wire it into `.git/hooks/pre-commit` (one-time per clone).
- `make pre-commit` — run every hook by hand, e.g. after editing the config itself.

## Architecture

```
    Unreal C++  ──FSimCommand──▶  USimSubsystem ──msgpack/TCP──▶  server.py
    (unreal/)   ◀──snapshot────   (worker thread)              ──▶  api.Simulation
                                                                    (sim/)
    unreal/Content/Map/*.json  ◀── make bake ── bake/src/geom/*.rs
```

The hard rule, unchanged from the Rust design: **the frontend contains no game
rules.** Every player action becomes a command; the visible layer is rebuilt
from the snapshot, so the screen cannot disagree with the simulation. The AI is
not special-cased — `ai.py` issues ordinary commands through the same
`rules.apply` path.

### `sim/src/tw_sim/`

- `api.py` — the `Simulation` facade, the **only** thing a frontend touches.
  Coarse-grained on purpose: `end_turn()` resolves the player's turn *and* every
  AI faction's, returning the whole event stream. There is deliberately no
  per-object accessor (no `army_position(42)` sixty times a second).
- `server.py` — ~100-line socket adapter onto the facade. Length-prefixed
  msgpack over loopback TCP, request/response, one in flight. `RuleError` comes
  back as `{"ok": false}`; anything else propagates so bugs are loud.
- `rules.py` — command dispatcher, the central file. `turn.py` — end-of-turn
  resolution and rotation. `state.py` — `GameState` and domain types.
  `ai.py`, `diplomacy.py`, `combat.py`, `economy.py` — subsystems.
  `command.py` / `event.py` — the boundary. `wire.py`, `ids.py`, `cli.py`.
- `config.py` + `data.py` — load `sim/campaign/balance.yaml` (tuning knobs) and
  `sim/campaign/britain.yaml` (factions, provinces, borders). Authored content
  is trusted: a bad reference **raises loudly** rather than returning an error value.

**Determinism is explicitly de-scoped** (`random.Random(seed)`, not ChaCha8) —
do not expect a seed to replay identically. `sim/tests/test_oracle.py` guards
*aggregate* campaign shape instead, against bounds recorded from the Rust engine
before it was deleted. Those numbers can no longer be re-derived: treat them as a
frozen baseline, and widen them only with a deliberate balance change in hand.

### `unreal/Source/TotalWarlike/`

- `Sim/SimSubsystem.{h,cpp}` — the game's single point of contact with the
  simulation. Everything reads the cached `GetSnapshot()` and reacts to
  delegates; nothing calls the transport per-frame, and the transport is not
  reachable from outside. Requests run on a worker thread; replies are
  marshalled to the game thread before any delegate fires, so subscribers may
  spawn/destroy actors freely.
- `Sim/MsgPack.{h,cpp}` — hand-rolled msgpack subset (Unreal ships none),
  deliberately free of Unreal types. Same for `Map/ProvinceLookup.h`. That is
  exactly what makes `make cpp-test` possible — **keep them engine-free.**
- `Sim/SimWire`, `SimTypes.h`, `SimTransport.h`, `SocketSimTransport` — framing,
  types, transport.
- `Map/CampaignGameMode` — there is **no `.umap`**. It spawns sun, sky, fog, sea
  and `ACampaignMap` at `BeginPlay`, so the game runs against a stock empty
  level. It also owns sidecar policy: `Auto` in the editor (reuse a running
  `make py-server`), `Spawn` in a packaged build.
- `Map/CampaignMap` — turns snapshot into actors. Static geography (terrain,
  rivers, forests) is built once; only ownership-dependent layers (border
  colours, markers) rebuild. Markers sync by **diffing ID sets**, not
  clear-and-rebuild — that is why armies glide instead of blinking, and why "no
  actor growth over 10 turns" is a testable property.
- `Map/MapData` — parses the baked `Content/Map/*.json` + `terrain.obj`. It
  transforms nothing; the baker already wrote Unreal space.
- `Map/CampaignPlayerController` — pan/zoom/click/end-turn. Decides *which*
  command to send, never whether it is legal; illegality is discovered by
  sending it and letting `rules.py` refuse. The one exception is a cheap
  pre-filter on adjacency, which answers what the click *meant*, not what is legal.
- `Map/CampaignHUD`, `EventText`, `MarkerActors`, `Ribbon` — presentation.
- `Tests/SimTransportTest.cpp` — automation tests (spawn a real sidecar).
  Note `unreal/Tests/wire_test.cpp` lives **outside** `Source/` because UBT
  compiles every `.cpp` under a module and that file has its own `main()`.

Content is data files and source, not binary assets — everything is constructed
in code. The single exception is the terrain material (needs a Custom HLSL node,
so a real `.uasset`); it is looked up softly and falls back to a flat colour.

### `bake/` and geometry

The simulation models provinces as a pure adjacency graph with **no
coordinates**. All world geometry is derived offline: `bake/src/main.rs` runs
`bake/src/geom/{terrain,regions,rivers,forests}.rs` once and writes
`terrain.obj`, `provinces.json`, `province_borders.json`, `rivers.json`,
`forests.json`. Those generators are inherited from the retired Godot client and
import nothing but serde, which is why the whole crate builds and bakes in ~12 s
from cold. An unoptimized `terrain.rs` takes ~70 s, which is why this is offline
rather than at load.

Coordinate conversion (Godot Y-up right-handed → Unreal Z-up left-handed cm)
happens **only** in the baker: `ue = (-godot.z, godot.x, godot.y) * SCALE`. If a
coordinate looks wrong on screen, there is exactly one place it went wrong.

`bake/src/geom/geo.rs` + `elev.bin` are generated by `bake/gen_geo.py` from
Natural Earth / terrarium tiles — do not hand-edit; regenerate.

Elevation is exaggerated ~60x (`terrain.rs::EXAG`). **Every height and slope
threshold in the terrain material is calibrated against that constant** — across
a language boundary, with nothing to catch a mismatch. Retuning one means
retuning the others.

## Workflow

**Changing a game rule:** edit `sim/src/tw_sim/`, add a test in `sim/tests/`,
run `make py-test`, sanity-check balance with `make py-sim`. No C++ changes
unless a new command/event field crosses the wire.

**Adding a command or event:** `command.py`/`event.py` → `rules.py` → wire
serialization (`wire.py`, `api.py`) → C++ `SimTypes.h`/`SimWire.cpp` →
whatever sends it. Verify with `make cpp-test` before `make unreal-test`.

**Changing the renderer:** edit `unreal/Source/TotalWarlike/Map/`,
`make unreal-build`, then `make unreal-play` (watch disk).

**Iterating on Python while the editor is open:** start `make py-server` first;
the editor attaches to it. Restart the sidecar and the editor picks it up on the
next campaign.

**Changing geometry:** edit `bake/src/geom/*.rs`, `make bake`, relaunch.
