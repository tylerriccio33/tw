---
name: run-tw-bake
description: Run and verify the tw-bake Rust geometry baker (bake/) that generates unreal/Content/Map/ — terrain mesh, province borders, rivers, forests. Use when asked to run, bake, regenerate, or smoke-test the map geometry / tw-bake.
---

Paths below are relative to the repo root `tw/` (this crate is a Cargo workspace
member, so `cargo run -p tw-bake` must be invoked from there, not from `bake/`).

`tw-bake` is a one-shot, offline CLI: no server, no GUI. It reads
`bake/land50.geojson` + `bake/src/geom/elev.bin`/`geo.rs`, runs the generators in
`bake/src/geom/`, and writes into `unreal/Content/Map/` (gitignored — safe to
regenerate freely). It depends on nothing but serde, so it builds and bakes in
~12s from cold.

## Run

```
make bake        # from repo root — same as: cargo run -p tw-bake
```

Verified output:

```
baking into .../unreal/Content/Map
  all 12 cities are on dry land
  winding agrees with normals over 10000 triangles
  terrain.obj            691200 vertices, 1379042 triangles (100.8 MB)
  terrain_meta.json      range -1000..2505 cm (0.0 MB)
  province_borders.json  21 border runs (0.0 MB)
  rivers.json            106 rivers (0.1 MB)
  forests.json           949 trees (0.1 MB)
  provinces.json         12 provinces, 5 colours (0.0 MB)
done.
```

## The terrain outputs

- **`terrain.obj`** — the terrain mesh, already in Unreal cm at the world origin.
  `tw/landscape.py` imports it as a Nanite static mesh; this is the only terrain
  path.
- **`terrain_meta.json`** — everything the Python side needs to place the terrain
  in Unreal-cm space and band the terrain material: `extent_cm`,
  `height_cm` (min/max/span), `terrain_exag`, and `bands_cm`. **Britain peaks at
  ~2505 cm**, well below the European snow anchor (4600 cm) — which is why the
  terrain material bands as fractions of the *actual* `height_cm` range, not the
  absolute anchors.

## Gotchas

- `provinces.json` is a dict (`{faction_colors, map_extent, meta, provinces}`), not
  a bare list — index into `["provinces"]` for the 12 provinces.
- The workspace root sets `opt-level = 3` for `tw-bake` under
  `[profile.dev.package.tw-bake]`; invoke from the repo root so that profile
  applies (~12s vs ~70s unoptimized).
- Output is gitignored, so re-running doesn't dirty `git status`.
