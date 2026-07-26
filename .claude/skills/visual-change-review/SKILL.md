---
name: visual-change-review
description: Workflow for making a visual/terrain change, rendering it, and reviewing the result. Use whenever asked to change something visual (materials, terrain, camera, lighting, world layout) and confirm how it looks — "screenshot this," "check how it looks," "review the visual change." Paths below are relative to repo root.
---

Straightforward loop for any visual change in this Godot campaign map. No custom driver needed — the render/review tooling already exists via `make`.

## Workflow

1. **Make the visual change** (terrain material, `world/*`, camera preset in `config/shots.json`, etc.).
2. **Render it**: `make sheet` — renders every preset in `config/shots.json` and tiles them into `shots/sheet.png` with labels. This is the default check; it needs no golden baseline.
3. **Review it**: read `shots/sheet.png` with the Read tool and actually look at it. Check the change did what was intended and didn't break other presets.
4. If comparing against the last-known-good state instead of eyeballing: `make diff` — prints a PSNR table and writes `shots/contact_sheet.png` comparing `shots/current/` to `shots/golden/`.
5. Once satisfied and want to promote the new render as the baseline: `make accept` — renders twice, refuses to promote if the two renders differ (catches nondeterminism), then copies `shots/current/*.png` to `shots/golden/`.

## Notes

- `make shot` alone just renders presets into `shots/current/` without tiling — `make sheet` is preferred for a quick look since it produces one labeled image.
- `make shot SET=debug.terrain_view=grey` applies a transient config override for one render without touching `world.json` — useful for isolating a terrain material change from lighting/trees/etc.
- Godot can exit 0 on a script error mid-render, silently producing a broken screenshot. If the sheet looks wrong (black bands, missing geometry), check for `SCRIPT ERROR`/`ERROR:` in the command's stderr before trusting the image.
- `make check` (fast, ~1s, no render) parses all `.gd` files for errors — run it first if a render comes back broken, to rule out a parse error before debugging visually.

## Screenshotting live gameplay (`campaign/`)

`make sheet`/`shot` only cover `world/*` terrain rendering via `tools/shoot.gd`'s config-driven SubViewport capture — they never touch the actual campaign game (`campaign/campaign.tscn`), which has its own UI/state and isn't reproducible from a config file. For a change in `campaign/` (UI, gameplay, the Rust GDExtension backend), use `make play-shot` instead:

- Launches `campaign.tscn` in a real window (bypasses the 128x128 window `project.godot` pins for `shoot.gd`'s benefit), waits ~6s to settle, takes an OS-level `screencapture` cropped to the window's bounds via AppleScript, writes `shots/play/play.png`, then kills the process.
- macOS only. Override window size with `RESOLUTION=WxH` (default `1280x800`) and settle time with `SETTLE_SECONDS` (default `6`) if the scene needs longer to come up.
- Read `shots/play/play.png` with the Read tool afterward, same as the sheet.
- Implementation: `tools/play_shot.sh`. Note it reads window `position`/`size` via System Events rather than `id of window` — Godot's SDL-backed window doesn't expose an AX window id (`-1728` on every attempt), but bounds work fine, so capture goes through `screencapture -R` (region crop) instead of `-l` (window id).
