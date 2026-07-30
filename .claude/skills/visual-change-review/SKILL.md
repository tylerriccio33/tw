---
name: visual-change-review
description: Workflow for making a visual change to the campaign map and reviewing the result. Use whenever asked to change something visual (2D province map, HUD, camera, army rendering) and confirm how it looks — "screenshot this," "check how it looks," "review the visual change." Paths below are relative to repo root.
---

Straightforward loop for any visual change in this Godot campaign map (`campaign/`). No custom driver needed — the render/review tooling already exists via `make`.

## Workflow

1. **Make the visual change** (`campaign/province_map.gd`, `campaign/campaign_ui.gd`, `campaign/region_area.gd`, army rendering, etc.).
2. **Render it**: `make shot` — builds the campaign Rust GDExtension, runs `campaign.tscn`, and saves the viewport's own texture to `shots/play/shot.png`. Prefer this. It renders from inside the engine, so it needs no OS permissions and can't come back black.
   - `make play-shot` instead captures the real window (chrome included) to `shots/play/play.png`, via `screencapture` and System Events. That needs **Accessibility and Screen Recording granted to whichever terminal is running it**; without them it either fails to find the window or silently writes a black frame. Use it only when the window chrome itself matters.
   - For a HUD-only change, `make hud-shot` reuses `play-shot`'s capture and crops it down to just the bottom HUD banner, written to `shots/play/hud.png`. Same permission caveat.
   - To see the whole map rather than the camera's default framing, pass a resolution matching the map's aspect, e.g. `make shot RESOLUTION=780x1100` — the camera cover-fits, so a tall viewport reveals the full landmass.
3. **Review it**: read `shots/play/shot.png` (or `shots/play/hud.png`) with the Read tool and actually look at it. Check the change did what was intended and didn't break anything else.
4. **For measurable questions** (padding/alignment off, screenshot regressed, image blurry), don't just eyeball it — use the `image-inspect` skill (`tools/image_inspect.py`) to get actual numbers: `compare` against a prior shot, `border`/`lines` for padding/alignment, `blur` for sharpness.

## Notes

- macOS only. Override window size with `RESOLUTION=WxH` (default `1280x800`) and settle time with `SETTLE_SECONDS` (default `6`) if the scene needs longer to come up. `hud-shot`'s `RESOLUTION` must match whatever `play-shot` was given, since the crop needs it to know where the OS titlebar chrome ends and game content starts.
- `make check` (fast, ~1s, no render) parses every `.gd` file in `tools/` and `campaign/` for errors — run it first if a render comes back broken, to rule out a parse error before debugging visually.
- Implementation: `tools/shoot.gd` (in-engine render), `tools/play_shot.sh` (OS screenshot), `tools/hud_shot.py` (crop). `play_shot.sh` reads window `position`/`size` via System Events rather than `id of window` — Godot's SDL-backed window doesn't expose an AX window id (`-1728` on every attempt), but bounds work fine, so capture goes through `screencapture -R` (region crop) instead of `-l` (window id).
