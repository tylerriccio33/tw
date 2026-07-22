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
- **`unreal/` (`TotalWarlike`)** — an Unreal 5.8 C++ renderer. Every click
  becomes a command; the visible layer is rebuilt from the snapshot, so the
  screen cannot disagree with the simulation. It contains no game rules.

`sim/server.py` speaks length-prefixed msgpack over loopback TCP. That is the
whole contract between them.

## Play

```sh
make unreal-play      # compile and launch (WASD pan, wheel zoom, click, Space)
make py-sim SEED=42   # or just watch an all-AI campaign headless
```

## Tests

```sh
make py-test    # the Python suite — this is the gate
make cpp-test   # the C++ bridge against a real sidecar, ~2 s, no Unreal needed
make unreal-test
```

## The map

Provinces are a pure adjacency graph with **no coordinates** — all world
geometry is derived offline by `make bake`, which writes `unreal/Content/Map/`.

The map is built from public-domain data, not noise. `bake/gen_geo.py` (stdlib
only — no GDAL, no numpy) downloads Natural Earth 50m land polygons for the
coastline and AWS Open Data terrarium tiles for elevation, then regenerates
`bake/src/geom/geo.rs` and `bake/src/geom/elev.bin` in place.

Elevation is exaggerated ~60x (`bake/src/geom/terrain.rs::EXAG`) — at true scale
the Alps would stand 1.6 units tall on a 1240-unit map and be invisible. **Every
height and slope threshold in the terrain material is calibrated against that
constant**; retuning one means retuning the others.

```sh
python3 bake/gen_geo.py   # regenerate geo.rs + elev.bin (rarely needed)
make bake                 # re-derive the Unreal map assets
```

Balance lives in `sim/campaign/balance.yaml`; the map and starting positions in
`sim/campaign/britain.yaml`.

See `CLAUDE.md` for the full architecture notes and workflows.
