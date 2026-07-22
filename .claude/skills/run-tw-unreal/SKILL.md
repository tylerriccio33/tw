---
name: run-tw-unreal
description: Build and test the TotalWarlike Unreal C++ bridge (unreal/) — run the engine-free bridge protocol test (make cpp-test), or build/play the full Unreal editor campaign map (make unreal-build / unreal-play) with disk-space monitoring. Use when asked to run, build, test, or screenshot the Unreal frontend / campaign map / sim bridge.
---

Paths below are relative to the repo root `tw/` (the `Makefile` targets used
here all assume that cwd).

**Disk is the binding constraint on this machine — check free space with
`df -h /` before any editor run.** CLAUDE.md notes a full editor launch has
twice filled the volume to the point that no shell command could run. Always
launch `unreal-build`/`unreal-play` in the background and be ready to `kill`
if free space drops toward ~3-4GB.

## Run (agent path): `make cpp-test` — no editor, no disk risk

This is the primary driver. It compiles just the engine-free half of the
bridge (`Sim/MsgPack.cpp`, `Map/ProvinceLookup.cpp`) with plain `clang++`,
starts a real `tw_sim` sidecar, and exercises the whole wire protocol —
codec round-trips, province hit-testing, init/move/end_turn — in about 2
seconds, with zero Unreal engine involvement.

```
make cpp-test
```

Verified output (excerpt):

```
codec
  ok   every integer width round-trips
  ...
sidecar listening on 65239
sidecar
  ok   init succeeds
  ok   britain has 12 provinces
  ok   britain has 5 factions
  ok   an illegal move is refused
       rule: NOT_ADJACENT
  ok   end_turn advances the turn
  ...
all checks passed
```

Reach for this first for anything touching `unreal/Source/TotalWarlike/Sim/`
or `Map/ProvinceLookup.*` — it is what CLAUDE.md calls out as the fast path
before a full `unreal-build`.

## Run (human path, verified here): full editor build + play

Requires the engine at `/Users/Shared/Epic Games/UE_5.8` (override with
`UE=<dir>`). Verified end to end in this session:

```
df -h /                                   # confirm headroom first
nohup make unreal-build > /tmp/ub.log 2>&1 &
# poll df -h / and /tmp/ub.log; "Result: Succeeded" in ~10-15s on a warm cache
nohup make unreal-play > /tmp/up.log 2>&1 &
# the window takes a few seconds to appear; bring it forward and screenshot:
osascript -e 'tell application "UnrealEditor" to activate'
screencapture -x /path/to/screenshot.png
```

`unreal-build` compiled and linked in ~12s here (`[1/3] Compile
ProvinceLookup.cpp`, `[2/3] Compile CampaignMap.cpp`, `[3/3] Link`,
"Result: Succeeded"). `unreal-play` then opened a real 1600x900 window
showing the baked campaign map — forests, rivers, settlement markers, and
the turn HUD (`turn 1 — England  [Space] end turn`); see
`verified-campaign-map.png` alongside this file for the actual captured
frame. Free disk went from 11GiB to 9.9GiB over the whole build+play+quit
cycle (shader/derived-data cache growth) — nowhere near the danger CLAUDE.md
warns about, but worth re-checking on a machine with less headroom.

To quit: `-game` mode has no in-window quit shortcut wired up (`Space` ends
the turn, it doesn't exit) — find the PID (`ps aux | grep UnrealEditor`,
look for the one running `-game -windowed`, not `UnrealEditorServices`) and
`kill` it. It exits cleanly.

## Test: `make unreal-test`

Runs the `TotalWarlike.Sim` automation suite headless
(`-ExecCmds="Automation RunTests TotalWarlike.Sim"`) against a real spawned
sidecar. Not run in this session (same disk-risk profile as
`unreal-build`/`unreal-play`, and `cpp-test` already covers the same bridge
surface faster) — attempt it the same way as `unreal-play` above
(background + `df -h /` polling) if you specifically need the in-editor
automation test rather than the standalone `cpp-test`.

## Gotchas

- `osascript -e 'tell application "System Events" to ...'` for window
  introspection fails with `execution error: ... is not allowed assistive
  access (-1728)` in this environment (no Accessibility permission granted).
  `tell application "UnrealEditor" to activate` works fine though (it's a
  plain Apple Event, not an Accessibility API call) — use that plus
  `screencapture -x` for a full-screen shot instead of trying to target a
  specific window.
- A bare `screencapture -x` right after launch is likely to capture whatever
  app was frontmost before the game launched (e.g. your editor), not the
  game window — `activate` UnrealEditor first, then capture.
- `ps aux | grep UnrealEditor` matches two processes: the actual game
  (`.../UnrealEditor.app/Contents/MacOS/UnrealEditor ... -game -windowed`)
  and an unrelated always-on `UnrealEditorServices` helper — kill the
  former, not the latter.
- `unreal-play` depends on `unreal-build` in the Makefile, so a plain
  `make unreal-play` always re-invokes the build step first; on a warm
  cache that's only a few seconds, not a full rebuild.
