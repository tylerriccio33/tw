---
name: run-tw-sim
description: Run, drive, and test the tw_sim Python campaign simulation (sim/) — start the msgpack/TCP sidecar, send init/command/end_turn requests programmatically, run the headless all-AI balance sim, or run the pytest suite. Use when asked to run, start, test, or exercise the tw simulation/sidecar/server.
---

Paths below are relative to the repo root `tw/`; the simulation itself lives
in `sim/`.

`tw_sim` is a headless Python simulation with no GUI. It is driven either as a
library, as a one-shot CLI (`tw_sim.cli`, all-AI balance runs), or as a
long-lived socket sidecar (`tw_sim.server`) that a frontend (normally Unreal)
talks to over length-prefixed msgpack/TCP. The driver in this skill
(`driver.py`) speaks that same protocol directly, so you can prove the sidecar
works without Unreal at all.

## Prerequisites

- `uv` (already on PATH as an alias in this environment)
- Python >=3.13 (`uv` resolves this itself via `pyproject.toml`)

No install step is needed beyond `uv run`, which syncs `sim/.venv` from
`uv.lock` on first use.

## Run (agent path): the sidecar driver

```
uv run --project sim python .claude/skills/run-tw-sim/driver.py smoke
```

This spawns `python -m tw_sim.server --port-file .sim-port.driver`, connects
over TCP, and exercises the real protocol: `init` (loads the `britain`
campaign, seed 42) -> inspects the snapshot -> `command` (moves the player's
first army into an adjacent province) -> `end_turn` -> `snapshot`. Verified
output looks like:

```
sidecar up on port 64911 (pid 5337)
init ok: 12 provinces, 5 factions
player faction: England, 1 armies
move army 0 0->1: ok=True
end_turn ok: 27 events, now turn 2
final snapshot fetched ok, turn=2
sidecar stopped
```

To drive it interactively instead of just running `smoke`, import
`SimClient`/`spawn_server` from `driver.py` in a REPL — `request()` takes any
of the four ops documented in `sim/src/tw_sim/server.py`'s docstring
(`init`, `command`, `end_turn`, `snapshot`).

## Direct invocation (no socket): headless all-AI campaign

For balance/behavior checks that don't need the wire protocol at all:

```
cd sim
uv run python -m tw_sim.cli --seed 42 --rounds 20
```

Verified output (seed 42, 20 rounds):

```
turn   1: Chester falls (Rebels -> Wales)
turn   2: Highlands falls (Rebels -> Scotland)
turn   5: England declares war on Wales
...
turn  11: Wales DESTROYED

=== after turn 20 (seed 42) ===
1 wars declared, 11 battles/assaults, 7 cities changed hands
  England            7 provinces, strength 4840, treasury 11377g
  Scotland           3 provinces, strength 4400, treasury 249g
  Ireland            2 provinces, strength 4520, treasury 295g
  rebels still hold 0 province(s)
```

Non-determinism note: this uses `random.Random(seed)`, not a portable PRNG —
re-running with the same seed will not reproduce byte-identical output (see
`sim/tests/test_oracle.py`, which checks aggregate bounds instead of exact
replay).

## Run (human path): standalone sidecar

```
cd sim
uv run python -m tw_sim.server --port-file .sim-port
```

Prints the chosen port and writes it to `.sim-port`; an already-open Unreal
editor picks it up on the next campaign instead of spawning its own. Useless
on its own without a client — this is what the driver above wraps.

## Test

```
cd sim
uv run pytest -q
```

Verified: `64 passed in 1.21s`.

## Gotchas

- `driver.py`'s `spawn_server` reads exactly one line of stdout to get the
  port — that line is the *only* thing `server.py` prints on stdout before
  going quiet (see `server.py`'s `serve()`), so don't add other startup
  logging ahead of it or the driver will parse the wrong line.
- The driver uses its own port file (`.sim-port.driver`) rather than
  `.sim-port`, so it won't collide with (or get confused by) a real
  `make py-server` you left running in another shell.
- `snapshot["armies"][i]["owner"]` must be matched against the player
  faction's `id`, not its list index — factions and their ids are not
  guaranteed to line up 1:1 once rebels/destroyed factions are involved.
