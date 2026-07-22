---
name: run-tw-bake
description: Run and verify the tw-bake Rust geometry baker (bake/) that generates unreal/Content/Map/ — terrain, province borders, rivers, forests. Use when asked to run, bake, regenerate, or smoke-test the map geometry / tw-bake.
---

Paths below are relative to the repo root `tw/` (this crate is a Cargo
workspace member, so `cargo run -p tw-bake` must be invoked from there, not
from `bake/`).

`tw-bake` is a one-shot, offline CLI: no server, no GUI, nothing to keep
running. It reads `bake/land50.geojson` + `bake/src/geom/elev.bin` /
`geo.rs`, runs the generators in `bake/src/geom/`, and writes five files into
`unreal/Content/Map/` (gitignored — safe to regenerate freely; see
`bake-458e5`'s "Untrack baked map content" commit).

## Prerequisites

- Rust via `cargo` (already on PATH; the workspace pins the toolchain in
  `rust-toolchain.toml`)

No other setup — the crate deliberately depends on nothing but
`serde`/`serde_yaml`/`serde_json`.

## Run (agent path): the smoke script

```
.claude/skills/run-tw-bake/smoke.sh
```

Deletes `unreal/Content/Map/`, runs `cargo run -p tw-bake`, and checks all
five output files exist, are non-empty, and that `provinces.json` parses with
exactly 12 provinces. Verified output:

```
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.10s
     Running `target/debug/bake`
baking into /Users/tylerriccio/Desktop/tw/unreal/Content/Map
  all 12 cities are on dry land
  winding agrees with normals over 10000 triangles
  terrain.obj            691200 vertices, 1379042 triangles (100.8 MB)
  province_borders.json  21 border runs (0.0 MB)
  rivers.json            106 rivers (0.1 MB)
  forests.json           949 trees (0.1 MB)
  provinces.json         12 provinces, 5 colours (0.0 MB)
done.
provinces.json: 12 provinces OK
SMOKE OK: all bake outputs present in unreal/Content/Map
```

## Run (human path)

```
make bake      # from repo root — same as: cargo run -p tw-bake
```

Cold build + bake took ~13s here (`1.11s` compile + `~12.6s` run); a warm
rerun (binary already built) took under a second before the bake itself ran.

## Gotchas

- `provinces.json` is a dict (`{faction_colors, map_extent, meta,
  provinces}`), not a bare list — a naive `len(json.load(...))` on the file
  gives you `4` (top-level keys), not the province count. Index into
  `["provinces"]` first.
- The workspace root `Cargo.toml` sets `opt-level = 3` for `tw-bake`
  specifically under `[profile.dev.package.tw-bake]` — running
  `cargo run -p tw-bake` from inside `bake/` directly (rather than the
  workspace root) can silently miss that profile override and fall back to
  the much slower unoptimized path CLAUDE.md warns about (~70s vs ~12s).
  Always invoke from the repo root.
- Output lands directly under `unreal/Content/Map/`; it is gitignored, so
  re-running this doesn't dirty `git status`.
