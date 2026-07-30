---
name: visual-change-review
description: Workflow for making a visual change to the campaign map and reviewing the result. Use whenever asked to change something visual (2D province map, HUD, camera, army rendering) and confirm how it looks — "screenshot this," "check how it looks," "review the visual change." Paths below are relative to repo root.
---

Straightforward loop for any visual change in this Godot campaign map (`campaign/`). No custom driver needed — the render/review tooling already exists via `make`.

## Workflow

1. **Make the visual change** (`campaign/province_map.gd`, `campaign/campaign_ui.gd`, `campaign/region_area.gd`, army rendering, etc.).
2. **Render it**: `make play-shot` — builds the campaign Rust GDExtension, launches `campaign.tscn` in a real window, waits for it to settle, and takes an OS-level screenshot to `shots/play/play.png`.
   - For a HUD-only change, `make hud-shot` reuses the same capture and crops it down to just the bottom HUD banner, written to `shots/play/hud.png`.
3. **Review it**: read `shots/play/play.png` (or `shots/play/hud.png`) with the Read tool and actually look at it. Check the change did what was intended and didn't break anything else.
4. **For measurable questions** (padding/alignment off, screenshot regressed, image blurry), don't just eyeball it — use the `image-inspect` skill (`tools/image_inspect.py`) to get actual numbers: `compare` against a prior shot, `border`/`lines` for padding/alignment, `blur` for sharpness.

## Notes

- macOS only. Override window size with `RESOLUTION=WxH` (default `1280x800`) and settle time with `SETTLE_SECONDS` (default `6`) if the scene needs longer to come up. `hud-shot`'s `RESOLUTION` must match whatever `play-shot` was given, since the crop needs it to know where the OS titlebar chrome ends and game content starts.
- `make check` (fast, ~1s, no render) parses every `.gd` file in `tools/` and `campaign/` for errors — run it first if a render comes back broken, to rule out a parse error before debugging visually.
- Implementation: `tools/play_shot.sh` (screenshot), `tools/hud_shot.py` (crop). `play_shot.sh` reads window `position`/`size` via System Events rather than `id of window` — Godot's SDL-backed window doesn't expose an AX window id (`-1728` on every attempt), but bounds work fine, so capture goes through `screencapture -R` (region crop) instead of `-l` (window id).
