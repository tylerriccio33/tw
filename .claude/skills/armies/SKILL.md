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

`spawn_starting_armies` (`godot_api.rs:419`) is called unconditionally at the end of both `start_game()` and `start_game_from_positions()` — one army per faction, named `"{faction} Army"`, planted on and garrisoned in that faction's *first* owned city. There's no recruitment system; this fixed one-army-per-faction roster is the entire order of battle for a campaign. If armies aren't appearing, the bug is almost never here — check that `campaign/campaign.tscn` is actually the scene being run (see `make play`; the editor's default Play button loads `tools/shoot.tscn` per `project.godot`'s `run/main_scene`, not the campaign).

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

1. `campaign_ui.gd::_unhandled_input` (~line 273) handles zoom, and clicks that missed every army marker (Godot delivers marker-hit clicks to `army_layer.gd::_on_marker_input` first via `gui_input`; only misses reach here).
2. Marker hits: `army_layer.gd::_on_marker_input` (line 255) — left-click on a friendly army selects it (`select()`, line 275); left-click on any army while one is already selected issues a move order onto its ground position; right-click on any army issues an attack/move order onto its ground position.
3. Ground misses: `campaign_ui.gd` calls `_army_layer.order_selected_at_screen(event.position)` on left-click (raycasts screen → world via `_ground_point`, `army_layer.gd:106`), or does nothing on right-click.
4. `order_selected_to(ground)` (`army_layer.gd:289`) guards on `_orders_locked` (set true while the AI is animating its turn), whose turn it is, and game-over, then calls `_manager.move_army(_selected_id, ground.x, ground.y)`.
5. That's the GDExtension binding `godot_api.rs::move_army` (line 351), which calls `Campaign::move_army` in Rust, emits `army_moved` (+ `army_battle` if the arrival fought, + `game_over` if that ended it), and returns success/failure.
6. Back in `order_selected_to`, a successful move immediately tries `garrison_army` in case it landed in a friendly city, then emits `state_changed` so `campaign_ui.gd` re-syncs the HUD.

`_manager` in GDScript is the GDExtension node instance exposing all the `#[func]`s above directly as methods — there's no separate manager/wrapper script.
