# Shots — the visual loop

Editing how this game *looks* is not a loop you can close with a test. It is
edit → view → iterate, and the "view" step has to be **one command, from a fixed
camera**, or two iterations are not comparable and the loop is just guessing.

```
make unreal-shots     # render every preset to current/   (~10s, headless)
make shots-diff       # what moved since golden/
make shots-bless      # accept current/ as the new golden/
```

`make unreal-shot SHOT=mountain` does one preset when you are iterating on one
thing.

## The presets

Defined in `Source/TotalWarlike/Map/ShotDirector.cpp`. Each is anchored to a
province **by name**, not by coordinates, so a rebake that moves the coastline
moves the shot with it.

| preset | what it is there to catch |
| --- | --- |
| `overview` | silhouette, sea colour, fog, faction ink across the whole island |
| `lowlands` | low green terrain, rivers, a settlement marker up close |
| `coast` | the land/sea edge — coastline blend and the water plane |
| `mountain` | the height and slope thresholds: rock, snow line, steep faces |
| `border` | a contested frontier — border colours and army markers |

They are chosen so that a change nobody meant to make shows up in at least one
frame. `TWShotsList` in the console prints the same table.

## Reading `shots-diff`

Two numbers per preset, and the point is the *contrast between them*:

- **mean** — average difference, 0-255. Tone: a tint, exposure, fog.
- **moved** — fraction of pixels past a threshold. Structure: geometry, a border
  line, a marker.

A snow-line tweak should light up `mountain` and leave `lowlands` near zero. If
it moves all five by a similar mean, it was a global tint, not the local change
it was supposed to be. That is the question a single screenshot cannot answer.

`current/` is gitignored; `golden/` is committed. Only bless with a deliberate
visual change in hand — the same discipline `test_oracle.py`'s bounds get.

## The tight loop (no relaunch)

A `.ush` edit does not need a fresh process. `r.ShaderDevelopmentMode=1` is set
in `Config/DefaultEngine.ini`, so:

```
make unreal-live          # one session, console enabled (backtick key)
```

then in the console, as many times as you like:

```
recompileshaders changed     # reload TerrainCommon.ush in place   (~2s)
TWView mountain              # jump to a preset
TWShot try1                  # capture the current view to current/try1.png
```

That is the difference between iterating three times and thirty. `TWShot`
deliberately does **not** move the camera, so you can nudge the view by hand and
still capture it.

## Known: the exit SIGTRAP

`UnrealEditor` reliably crashes in `FMacApplication`'s destructor on the way out
of a windowed `-game` session — *after* every PNG has been written. `make
unreal-shots` therefore ignores the exit code and checks the real contract:
every requested shot is on disk. A missing shot still fails the target.
