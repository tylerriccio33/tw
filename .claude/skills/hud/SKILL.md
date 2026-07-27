---
name: hud
description: How to edit the campaign HUD (the bottom banner, city panel, buttons, labels over the 3D campaign map). Use whenever asked to change, add, or debug HUD/UI elements in campaign/ — buttons, labels, panels, layout, scaling, or click behavior.
---

Reference for editing the Total War-style HUD in the live campaign game (`campaign/campaign.tscn`). The HUD is mostly built in code, not in the scene file — read this before touching it, since the natural instinct to open the `.tscn` in an editor and drag nodes only gets you part of the picture.

## Where things live

- **Scene:** `campaign/campaign.tscn` — declares the static skeleton: `CityMarkers` (Control, holds city dots/labels), `UI` (Control, holds `StatusLabel`, `LogLabel`, `Controls` HBoxContainer with `TargetOption`/`AttackButton`, and an empty `BottomBanner` Control).
- **Script:** `campaign/campaign_ui.gd` — everything else. The bottom banner (city info panel, buildings tray, END TURN button) is built procedurally in `_build_bottom_banner()` (and its helpers `_build_city_panel()`, `_build_buildings_panel()`, `_build_end_turn_banner()`), called once from `_ready()`. There's no equivalent of these nodes sitting in the `.tscn` — if you want to add a new banner widget, add it in GDScript alongside the existing `_build_*` methods, not in the scene editor.
- Per-turn data refresh (labels/stat values) happens in `_refresh_bottom_banner()`, called from `_refresh()` on every `turn_started`/`battle_resolved`/`game_over` signal — it only *writes into* widgets already built by `_build_bottom_banner()`, it doesn't rebuild them.

## Adding or editing a widget

1. Find the right `_build_*` method (city panel vs. buildings tray vs. end-turn banner) and add your node there, following the existing pattern: plain `Control`/`PanelContainer`/`VBoxContainer` nodes styled with `_style_box()` and `_set_font()` helpers (`FONT_MEDIUM`/`FONT_SEMIBOLD`/`FONT_BOLD`, `HUD_BLUE`/`HUD_CREAM`/`HUD_MAROON` constants).
2. Position it with `_anchor_rect(control, left, top, right, bottom)` — **fractional anchors (0.0–1.0 of the viewport), not pixel offsets**. This is what keeps the banner's proportions correct across window sizes; don't use `offset_left/top/right/bottom` (`layout_mode = 0`) for anything in the bottom banner.
3. If the widget needs live data, store a reference to it (see `_city_panel_name_label`, `_city_stat_value_labels`, etc. near the top of the script) and write to it from `_refresh_bottom_banner()`.
4. Wire up button signals explicitly in `_ready()` or the relevant `_build_*` method, e.g. `end_turn_button.pressed.connect(_on_end_turn_pressed)` — Godot won't auto-connect anything.

## The top-left widgets are different from the bottom banner

`StatusLabel`, `LogLabel`, and `Controls` (`TargetOption`/`AttackButton`) are declared directly in `campaign.tscn` with `layout_mode = 0` and fixed pixel offsets, not fractional anchors like the bottom banner. That's an existing inconsistency, not a bug to silently "fix" — know it's there before assuming all HUD elements behave the same way.

## Scaling with window size

`project.godot`'s default window size is `1280x800`; `make play`'s `--resolution` flag overrides it per launch. To make the whole HUD (fonts, buttons, banner cards) scale proportionally when the window is resized, `_ready()` in `campaign_ui.gd` sets canvas-item content scaling on the window:

```gdscript
get_window().content_scale_mode = Window.CONTENT_SCALE_MODE_CANVAS_ITEMS
get_window().content_scale_size = Vector2i(1280, 800)
get_window().content_scale_aspect = Window.CONTENT_SCALE_ASPECT_EXPAND
```

`1280x800` is the baseline every pixel offset/`custom_minimum_size` in the HUD code was tuned against (it's also the Makefile's default `RESOLUTION`). If you add new pixel-sized widgets, lay them out assuming this baseline — they'll scale automatically with everything else.

## Verifying a change

- `make check` — fast parse-only check, catches GDScript errors before you spend time rendering.
- `make play` — opens the live campaign map in a real window so you can click through it by hand.
- `make play-shot` — same, but headless-ish: launches, settles ~12s, takes an OS-level screenshot to `shots/play/play.png`, kills the process. Read the PNG afterward. Override size with `RESOLUTION=WxH`, settle time with `SETTLE_SECONDS`.
- `make hud-shot` — like `play-shot` but crops to just the bottom banner, useful for reviewing a HUD-only tweak without eyeballing a full map screenshot.
- To confirm a button actually responds (not just that it's visually present), simulate a real click rather than trusting the screenshot alone: `brew install cliclick` if not present, resolve the window's screen bounds via `osascript`/System Events (see `tools/play_shot.sh` for the exact AppleScript), then `cliclick c:<x>,<y>` at the button's on-screen point (remember Retina screenshots are 2x the point coordinates System Events reports), and re-screenshot to confirm state changed (e.g. turn counter incremented).
