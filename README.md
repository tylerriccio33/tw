# tw — a Total War-style campaign game

A minimal grand campaign over Britain: provinces and factions plus rebels, with
diplomacy, trade, cities, sieges, and single-unit-type warfare (no tactical
battles — the bigger effective force wins). You play England; everyone else is a
heuristic AI.

Two halves, meeting at a narrow socket interface:

- **`sim/` (`tw_sim`)** — the Python simulation, and the source of truth for
  every game rule. Frontends touch exactly one thing, the `Simulation` facade in
  `api.py`; the AI issues ordinary commands through the same `rules.apply` path
  the player does.
- **`unreal/` (`TotalWarlike`)** — a Python-driven Unreal 5.8 frontend with **no
  C++ module.** Every actor, material, light and screenshot is built by the `tw`
  Python package inside the editor, driven by the `twctl` CLI (`tools/twctl`). The
  visible layer is rebuilt from the snapshot, so the screen cannot disagree with
  the simulation. It contains no game rules.

`sim/server.py` speaks length-prefixed msgpack over loopback TCP; `tw/simbridge.py`
is the pure-Python client. That is the whole contract between them.

## Play

```sh
make build            # headless: build + save the whole world (needs UE_5.8)
make shot             # render the fixed-camera preset screenshots
make live             # a persistent editor for the tight loop (make exec CODE=...)
make py-sim SEED=42   # or just watch an all-AI campaign headless
```

## Tests

```sh
make py-test      # the Python suite — this is the gate
make bridge-test  # the pure-Python sim bridge + msgpack codec vs a real sidecar, ~2 s, no editor
```

## The map

Provinces are a pure adjacency graph with **no coordinates** — all world
geometry is derived offline by `make bake`, which writes `unreal/Content/Map/`.

The map is built from public-domain data, not noise. `bake/gen_geo.py` (stdlib
only — no GDAL, no numpy) downloads Natural Earth 50m land polygons for the
coastline and AWS Open Data terrarium tiles for elevation, then regenerates
`bake/src/geom/geo.rs` and `bake/src/geom/elev.bin` in place.

Elevation is exaggerated (`bake/src/geom/terrain.rs::EXAG`) — at true scale
terrain is invisible on a map this wide. The baker writes the resulting height
range into `terrain_meta.json` and the terrain material bands as fractions of it.

```sh
python3 bake/gen_geo.py   # regenerate geo.rs + elev.bin (rarely needed)
make bake                 # re-derive the Unreal map assets
```

Balance lives in `sim/campaign/balance.yaml`; the map and starting positions in
`sim/campaign/britain.yaml`.

See `CLAUDE.md` for the full architecture notes and workflows.
