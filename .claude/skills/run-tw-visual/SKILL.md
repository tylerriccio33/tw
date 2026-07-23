---
name: run-tw-visual
description: Build, render, and iterate on the tw campaign map's visuals through the Python-driven Unreal frontend (twctl). Use when asked to build the world, render/screenshot the campaign map, run the visual loop, drive the editor over Python, or test the pure-Python sim bridge.
---

The tw Unreal frontend has **no C++ module** — everything is built by the `tw`
Python package inside the editor, driven by the `twctl` CLI. All commands below
are Makefile targets run from the repo root `tw/`. Override the engine location
with `TW_UE=<dir>` (default `/Users/Shared/Epic Games/UE_5.8`).

**Disk is the binding constraint — check `df -h /` before any editor run.** A full
editor launch has twice filled the volume until no shell command could run.
`twctl` already guards this (refuses to launch under ~4 GB free, kills a headless
run under ~3 GB), but launch editor commands in the background and watch anyway.

## The fast gate (no editor, no disk risk): `make bridge-test`

Reach for this first for anything touching `tw/simbridge.py` or the wire protocol.
It runs the pure-Python bridge and its vendored msgpack codec against a real
`tw_sim` sidecar in ~2s, cross-checking the codec against the reference `msgpack`
library. This is the role the old `make cpp-test` played.

```
make bridge-test
```

Verified output ends with: `init succeeds`, `britain has 12 provinces`,
`britain has 5 factions`, `end_turn advances the turn`, `an illegal move is
refused`, `all checks passed`.

## Build the world (headless): `make build`

Builds the whole campaign map from the bake outputs + a sim snapshot (or a neutral
snapshot if no sidecar is up) and saves the level. Run `make bake` first if
`unreal/Content/Map/` is missing.

```
make bake        # if the map geometry isn't baked yet
make build       # headless UnrealEditor-Cmd -run=pythonscript entry/build.py
```

## See the visuals: `make shot`

**This is how to look at the game.** Renders fixed-camera presets to
`unreal/Shots/current/`, then diff against the committed `golden/`.

```
make shot                       # all presets: overview, lowlands, coast, mountain, border
make shot SHOTS="mountain border"
make shots-diff                 # what moved vs golden/
make shots-bless                # accept current/ as the new golden/
```

The PNGs are directly readable — open them and compare to `target-state.png`,
which is the primary acceptance signal (this slice is visuals-first). Two
identical runs diff to ~0, so any non-zero number in `shots-diff` is a real
change. A shot missing from disk is a hard failure (the editor can SIGTRAP in
teardown on macOS *after* writing every PNG, so the exit code is ignored and the
files are checked directly).

## The tight loop: `make live` + `make exec`

Keep ONE editor up and push Python into it over remote execution, so a material
edit costs a function re-run (~1-2s) instead of a relaunch.

```
# window A:
make live
# window B (edit tw/materials/terrain.py, then):
make exec CODE='import importlib, tw.materials.terrain as t; importlib.reload(t); t.build()'
make shot SHOTS=mountain
```

## Iterating on Python while the editor is open

Start `make sim` first (writes `sim/.sim-port`); a live editor's `tw.simbridge`
attaches to it. Restart the sidecar and the editor picks it up on the next
snapshot — restart Python without closing the editor.

## Gotchas

- **The scripted Landscape import (`tw/landscape.py::_import_heightmap`) is the one
  call to confirm against the installed engine's Python API.** If UE 5.8 doesn't
  expose it, `landscape.build()` logs a warning and falls back to importing
  `terrain.obj` as a static mesh — the terrain material reads world-Z, so both
  paths look right. If terrain is missing, check the editor log for that fallback.
- Everything the toolkit spawns is tagged `tw`; a rebuild clears exactly the prior
  run's actors. If you see doubled geometry, something spawned untagged.
- `make build`/`shot`/`live`/`assets` all need the engine; `make bridge-test`,
  `make bake`, `make py-*` do not.
