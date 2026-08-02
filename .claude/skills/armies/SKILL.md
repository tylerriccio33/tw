---
name: armies
description: How the campaign army system works — state model, movement, battles/sieges, spawning, and the 2D marker + 3D knight rendering layers. Use whenever asked to change, add, or debug army behavior, movement/click orders, battle resolution, or army rendering in campaign/ or rust/campaign/.
---

Reference for the campaign army system. The rules live entirely in Rust; the GDScript layer only renders state and forwards orders. Read this before diving into the source — it saves re-deriving the whole call chain from scratch.

## Where things live

- **Rules/state:** `rust/campaign/src/model.rs` — `Army` struct, `Campaign::move_army`, `Campaign::garrison_army`, battle resolution.
- **GDExtension boundary:** `rust/campaign/src/godot_api.rs` — `#[func]` bindings GDScript calls, `get_state()` snapshot shape, signal emission (`army_moved`, `army_battle`, `game_over`).
- **2D markers/input:** `campaign/army_layer.gd` — click targets, selection, range ring, move/attack orders, marker animation.
- **3D models:** `campaign/army_models.gd` — one tinted knight model per army, purely visual, no input.
- **Scene owner:** `campaign/campaign_ui.gd` — owns both layers, wires camera-level mouse input (clicks that miss every marker).

## Data model (`model.rs:52-66`)

`Army { id, name, owner: FactionId, position: (f32,f32), movement, max_movement, garrisoned: Option<CityId>, alive }`. No composition/strength stat — battles are a coin flip, not attrition. `position` is continuous world (x, z), not tile-based. `garrisoned` is a pure flag: set by `garrison_army`, cleared by any `move_army` call.

`godot_api.rs::get_state()` (line 244) returns the dict GDScript polls every refresh:
```
{turn, max_turns, current_faction, game_over, winner,
 factions: [{id, name, money, alive, cities}],
 cities: [{id, name, income, owner, x, y, garrison}],
 armies: [{id, name, owner, x, y, movement, max_movement, garrisoned}]}
```
Only `alive` armies are included (`godot_api.rs:278`). There's no push/diff — GDScript just re-derives its rendering from a fresh snapshot each call.

## Spawning

`spawn_starting_armies` (`godot_api.rs:419`) is called unconditionally at the end of both `start_game()` and `start_game_from_positions()` — one army per faction, named `"{faction} Army"`, planted on and garrisoned in that faction's *first* owned city. There's no recruitment system; this fixed one-army-per-faction roster is the entire order of battle for a campaign. If armies aren't appearing, the bug is almost never here — check that `campaign/campaign.tscn` is actually the scene being run (see `make play`; `project.godot`'s `run/main_scene` points at it).

## Movement (`model.rs:302-356`, `Campaign::move_army`)

Continuous, not tile/pathfound: straight line from current position toward the target, **one movement point spent per world unit of distance**, no terrain modifier (mountains/roads/ocean all cost the same). If the order's distance exceeds remaining `movement`, the army travels as far as it can along that exact heading and stops with an empty pool — the order is clamped, never rejected outright, unless `movement <= 0.0` already (then it's an error). Fails if it's not the owning faction's turn or the campaign is over. Any move clears `garrisoned`.

After moving, `resolve_arrival` (`model.rs:398`) checks what the army landed on/near, **city takes priority over army**:
1. Standing within `CITY_RADIUS` of an enemy city → `resolve_siege` (`model.rs:466`).
2. Otherwise within `ENGAGE_RADIUS` of a living enemy army → `resolve_field_battle` (`model.rs:431`).

Both are a `rng.gen_bool(0.5)` coin flip:
- **Field battle**: loser's army is destroyed (`alive = false`). Winner holds ground. Never eliminates a faction by itself.
- **Siege**: attacker win → city changes hands, its garrison (if any) is destroyed, and if the defending faction now owns zero cities the whole faction is marked dead and *all* its armies disband. Attacker loss → attacking army destroyed, city unchanged.

`garrison_army` (`model.rs:360`) is a separate explicit call: snaps an army standing within `CITY_RADIUS` of one of its own ungarrisoned cities to that city's centre and sets `garrisoned`. It's invoked automatically by `army_layer.gd::order_selected_to` right after a successful move, so marching into your own empty city auto-garrisons — no separate player action needed.

AI turns: `run_ai_turn` (`model.rs:528`, bound at `godot_api.rs:380`) plays every army the current (non-player) faction owns — each one either charges the nearest reachable enemy city/army or wanders a random heading, decided by coin flip — through the same `move_army`/battle path, so AI and player orders are indistinguishable downstream.

## Rendering: two layers reading the same state, one owning input

`army_layer.gd` owns a `Dictionary _ground` mapping army id → **currently drawn** (possibly mid-tween) (x, z) position — this deliberately lags the model, which teleports an army the instant `move_army` resolves. `army_models.gd::sync(state, ground_positions)` is called every refresh from `campaign_ui.gd` with `army_layer.ground_positions()` as the second argument, so the 3D knight always sits exactly where the 2D disc marker is currently drawn, not where the Rust model says the army actually is. World Y (height) comes from `_terrain.height_at(x, z)` in both layers (`army_layer.gd:94-100`, `army_models.gd:43-49`); the 2D layer adds a `LIFT` of 14 units so the disc clears the slope, the 3D model sits flush on the ground.

`army_layer.gd::_on_army_moved` (line 311, fired by the Rust `army_moved` signal) tweens `_ground[army_id]` from `from` to `to` over `MOVE_SECONDS` (0.7s) and calls `project()` every frame to re-screen-project the marker from the current camera. `army_models.gd` has no tween of its own — it just re-reads `_ground` each `sync()`.

Only `army_layer.gd` takes mouse input (`mouse_filter = STOP` on each marker `Panel`); `army_models.gd` is purely visual and never touches input.

## Input → order call chain

1. Army/city marker hits are Controls (`mouse_filter = STOP`), so Godot's GUI system delivers those synchronously via `gui_input`/`_gui_input` before anything else sees the click.
2. Marker hits: `army_layer.gd::_on_marker_input` — left-click on a friendly army selects it (`select()`, which also emits `army_selected` for the HUD - see below); left-click on any army while one is already selected issues a move order onto its ground position; right-click on any army issues an attack/move order onto its ground position.
3. Province clicks (no marker under the mouse): resolved **synchronously** in `campaign_ui.gd::_unhandled_input`, via `_province_map.province_at_local_point(local_pos)` — a plain point-in-polygon test against the same ring data `region_area.gd`'s `CollisionPolygon2D`s are built from. If a province is hit, it calls `_on_region_clicked(province_id)` directly. If nothing is hit at all (water/off-map), it deselects.
4. `_on_region_clicked`: if an army is selected (`_army_layer.selected_army_id() != -1`), resolves the clicked province to a world target (its city if it has one, else its centroid - `_move_target_for_province`) and calls `_army_layer.order_selected_to(world_pos)`. Otherwise it selects whatever city/province is there for the HUD.
5. `order_selected_to(ground)` (`army_layer.gd`) guards on `_orders_locked` (set true while the AI is animating its turn), whose turn it is, and game-over, then calls `_manager.move_army(_selected_id, ground.x, ground.y)`.
6. That's the GDExtension binding `godot_api.rs::move_army`, which calls `Campaign::move_army` in Rust, emits `army_moved` (+ `army_battle` if the arrival fought, + `game_over` if that ended it), and returns success/failure.
7. Back in `order_selected_to`, a successful move immediately tries `garrison_army` in case it landed in a friendly city, then emits `state_changed` so `campaign_ui.gd` re-syncs the HUD. `army_layer.gd::select()` separately emits `army_selected(army_id)` (`-1` on deselect) so `campaign_ui.gd::_on_army_selected` can mirror the selection into its own `_selected_army_id` and refresh the bottom-banner HUD to show army info - army_layer has no HUD of its own.

`_manager` in GDScript is the GDExtension node instance exposing all the `#[func]`s above directly as methods — there's no separate manager/wrapper script.

### Gotcha: province clicks are NOT resolved through the Area2D signal

`region_area.gd` (one per province) still has a working `Area2D` with a `clicked` signal relayed up to `province_map.gd`'s `region_clicked`, and it looks like the obvious place to hook move orders. **Don't wire `region_clicked` to `_on_region_clicked`** — 2D physics picking is deferred to the next physics step, unlike Control `gui_input` which resolves synchronously in the same input pass. That means a click reaches `campaign_ui.gd::_unhandled_input`'s "missed everything" fallback *before* the Area2D's `clicked` signal ever fires for the same click, so anything driven off that signal loses the race every time: it looks like "select an army, click a neighboring province, and it just reselects the province instead of moving" (the army gets deselected by `_unhandled_input` before the delayed signal arrives to move it). This is why step 3 above does its own synchronous point-in-polygon test instead of waiting on the signal. If you're tempted to reconnect `region_clicked` for something else, keep it away from move/select logic, or you'll get an intermittent double-fire once the delayed signal does eventually arrive (e.g. re-issuing a move order from the army's new post-move position, silently deselecting it right after a successful move).

### Gotcha: a full-rect Control with the default `mouse_filter` blocks everything under it

`campaign.tscn`'s `UI` node (holding the top bar/bottom banner/turn indicator) is a full-viewport-anchored `Control` with no visuals of its own. Control's default `mouse_filter` is `STOP`, so left as-is it silently swallows every click over the *entire map* that doesn't land on one of its own buttons/panels — city markers and provinces stop responding to clicks entirely, while army markers (added even later, so they sit on top of it) keep working, which makes it look like an army-selection-specific bug rather than the actual full-screen click blocker it is. `campaign_ui.gd::_ready()` explicitly sets `_ui.mouse_filter = Control.MOUSE_FILTER_IGNORE` (alongside the same fix already done for `cities_root`) — if you ever recreate this node or add another full-rect Control as a HUD anchor point, it needs the same treatment.
