extends Control
## Army pieces on the campaign map: draws one marker per living army, animates
## them along the moves the Rust model reports, and turns player clicks into
## move orders.
##
## Movement is province-to-province, not continuous: a selected army can hop
## into its current province's neighbors (and further, neighbors-of-
## neighbors, while its move budget lasts), landing on the target province's
## city regardless of where in it the click actually fell. Every rule of that
## lives in Rust (`Campaign::move_army`/`reachable_provinces`); this script
## only draws pieces, highlights reachable land, and forwards orders.
##
## The parent (campaign_ui.gd) owns the manager, the province map (for
## highlighting reachable provinces) and the world<->screen projection (plain
## 2D pan/zoom, no camera object), and hands them over in setup(); this node
## reports back through `log_message` and `state_changed` rather than
## reaching up into its parent.

signal log_message(text: String)
## Fired whenever selection changes (army_id == -1 means deselected), so the
## HUD owner (campaign_ui.gd) can refresh its bottom info panel to match -
## army_layer has no HUD of its own, it only owns the 2D markers/input.
signal army_selected(army_id: int)
## Emitted when an order this node issued changed the campaign, so the parent
## can re-render the rest of the HUD.
signal state_changed

const ArmyMarker := preload("res://campaign/army_marker.gd")

## Order cursor shown while an army is selected, and its green "this would
## move you" variant for when the mouse sits over a reachable city. Both from
## Kenney's CC0 Cursor Pack - see assets/ui/cursors/LICENSE.txt.
const CURSOR_ORDER := preload("res://assets/ui/cursors/tool_sword_a.png")
const CURSOR_ORDER_REACHABLE := preload("res://assets/ui/cursors/tool_sword_a_noop.png")
const CURSOR_HOTSPOT := Vector2(4.0, 4.0)
## How close the mouse has to sit to a reachable city's marker, in screen
## pixels, to count as "hovering a valid move target" for the cursor.
const REACHABLE_HOVER_RADIUS := ArmyMarker.DISC_RADIUS * 2.0

const PLAYER_FACTION := 0
const MOVE_SECONDS := 0.7  # how long one army's move animation takes
## A same-owner army starts standing exactly on its capital's city marker
## (see godot_api::spawn_starting_armies), so the piece is nudged this far
## from its true ground position purely for the draw - it would otherwise sit
## fully behind the keep icon. Sync/order/move math all stay on the
## un-nudged ground position; only project() applies this.
const DRAW_OFFSET := Vector2(20.0, -22.0)

var _manager: Node
var _world_to_screen: Callable
var _screen_to_world: Callable
var _faction_colors: Array[Color] = []
## The 2D province polygons, so a selected army's reachable provinces can be
## tinted - see province_map.set_highlighted_provinces. Null is fine (no
## highlighting) so tests/tools that build this node without a province map
## still work.
var _province_map: Node

var _markers: Dictionary = {}
## Animated source of truth for where each piece is *drawn*, as world (x, z).
## The tween writes into this and project() turns it into screen space, so
## panning or zooming mid-animation stays correct. It deliberately lags the
## model, which teleports an army the instant its order resolves.
var _ground: Dictionary = {}
var _tweens: Dictionary = {}
var _selected_id: int = -1
var _orders_locked := false

## Tracks which custom cursor (if any) is currently applied, so we only call
## Input.set_custom_mouse_cursor when the state actually changes.
enum CursorState { NONE, ORDER, ORDER_REACHABLE }
var _cursor_state: CursorState = CursorState.NONE


func setup(
	manager: Node,
	world_to_screen: Callable,
	screen_to_world: Callable,
	faction_colors: Array[Color],
	province_map: Node = null
) -> void:
	_manager = manager
	_world_to_screen = world_to_screen
	_screen_to_world = screen_to_world
	_faction_colors = faction_colors
	_province_map = province_map

	set_anchors_preset(Control.PRESET_FULL_RECT)
	# IGNORE on the container only - the markers themselves still take clicks,
	# and anything that misses one falls through to the parent's
	# _unhandled_input, which is where move orders and deselection land.
	mouse_filter = Control.MOUSE_FILTER_IGNORE

	_manager.army_moved.connect(_on_army_moved)
	_manager.army_battle.connect(_on_army_battle)


## Blocks player orders while the AI is playing its turns.
func set_orders_locked(locked: bool) -> void:
	_orders_locked = locked


## Swaps in the order cursor whenever an army is selected, and its green
## variant when the mouse is over a city the army could actually move to this
## turn. Runs every frame since it depends on live mouse position, not on
## state that changes through sync().
func _process(_delta: float) -> void:
	_set_cursor(_cursor_state_for(get_global_mouse_position()))


## Pure decision behind _process, split out so a test can drive it with an
## arbitrary mouse position instead of the real (unit-test-hostile) one.
func _cursor_state_for(mouse: Vector2) -> CursorState:
	if _selected_id == -1 or _orders_locked:
		return CursorState.NONE

	var reachable: Array = _manager.reachable_provinces(_selected_id)
	for city in _manager.get_state().get("cities", []):
		if not reachable.has(int(city.get("province", -1))):
			continue
		var screen: Vector2 = _world_to_screen.call(Vector2(float(city["x"]), float(city["y"])))
		if mouse.distance_to(screen) <= REACHABLE_HOVER_RADIUS:
			return CursorState.ORDER_REACHABLE

	return CursorState.ORDER


func _set_cursor(state: CursorState) -> void:
	if state == _cursor_state:
		return
	_cursor_state = state
	match state:
		CursorState.NONE:
			Input.set_custom_mouse_cursor(null)
		CursorState.ORDER:
			Input.set_custom_mouse_cursor(CURSOR_ORDER, Input.CURSOR_ARROW, CURSOR_HOTSPOT)
		CursorState.ORDER_REACHABLE:
			Input.set_custom_mouse_cursor(
				CURSOR_ORDER_REACHABLE, Input.CURSOR_ARROW, CURSOR_HOTSPOT
			)


## Tints the selected army's reachable provinces on the 2D map, or clears
## every highlight when nothing's selected. Called from select() and sync(),
## since moves_left (and so what's reachable) can change without a reselect.
func _update_highlighted_provinces() -> void:
	if _province_map == null:
		return
	if _selected_id == -1:
		_province_map.set_highlighted_provinces([])
	else:
		_province_map.set_highlighted_provinces(_manager.reachable_provinces(_selected_id))


func selected_army_id() -> int:
	return _selected_id


func _exit_tree() -> void:
	Input.set_custom_mouse_cursor(null)
	if _province_map != null:
		_province_map.set_highlighted_provinces([])


## ---------------------------------------------------------------------------
## Markers
## ---------------------------------------------------------------------------


func _ensure_marker(army: Dictionary) -> Control:
	var id: int = int(army["id"])
	if _markers.has(id):
		return _markers[id]

	var marker := ArmyMarker.new()
	marker.setup()
	marker.gui_input.connect(_on_marker_input.bind(id))
	add_child(marker)
	_markers[id] = marker
	return marker


## Rebuilds the pieces from a state snapshot: creates any new marker, restyles
## the rest, and drops the ones whose army died. Armies that are mid-animation
## keep their drawn position, so a refresh can't teleport a piece that is still
## visibly marching.
func sync(state: Dictionary) -> void:
	var live: Dictionary = {}
	for army in state.get("armies", []):
		var id: int = int(army["id"])
		live[id] = true
		var marker: ArmyMarker = _ensure_marker(army)
		marker.set_faction_color(_faction_colors[int(army["owner"]) % _faction_colors.size()])
		marker.set_selected(id == _selected_id)
		marker.tooltip_text = (
			"%s\n%d / %d moves%s"
			% [
				army["name"],
				int(army["movement"]),
				int(army["max_movement"]),
				"\nGarrisoned" if int(army["garrisoned"]) != -1 else "",
			]
		)
		if not _tweens.has(id):
			_ground[id] = Vector2(float(army["x"]), float(army["y"]))

	for id: int in _markers.keys():
		if not live.has(id):
			_remove_marker(id)

	if _selected_id != -1 and not live.has(_selected_id):
		_selected_id = -1

	project()
	_update_highlighted_provinces()


func _remove_marker(id: int) -> void:
	if _tweens.has(id):
		var tween: Tween = _tweens[id]
		if is_instance_valid(tween):
			tween.kill()
		_tweens.erase(id)
	if _markers.has(id):
		var marker: ArmyMarker = _markers[id]
		marker.queue_free()
		_markers.erase(id)
	_ground.erase(id)


## Screen-projects every piece from the current camera. Called on any camera
## change, and every frame while a piece is animating.
func project() -> void:
	for id: int in _markers.keys():
		var marker: ArmyMarker = _markers[id]
		marker.visible = true
		marker.position = (
			_world_to_screen.call(_ground[id]) + DRAW_OFFSET - marker.anchor_offset()
		)


func _army_field(army_id: int, key: String) -> float:
	if army_id == -1:
		return 0.0
	for army in _manager.get_state().get("armies", []):
		if int(army["id"]) == army_id:
			return float(army[key])
	return 0.0


## ---------------------------------------------------------------------------
## Orders
## ---------------------------------------------------------------------------


func _on_marker_input(event: InputEvent, army_id: int) -> void:
	if not (event is InputEventMouseButton and event.pressed):
		return
	if event.button_index == MOUSE_BUTTON_LEFT:
		# Left-clicking our own army always (re)selects it, even if a
		# different friendly army was already selected - otherwise clicking
		# army B while army A is selected would march A onto B instead of
		# switching command to B. Left-clicking an enemy piece while we have
		# an army selected is a move order onto that ground (which resolves
		# the same as any other march); otherwise it's just an (ignored)
		# selection of an uncommandable piece.
		var is_own := int(_army_field(army_id, "owner")) == PLAYER_FACTION
		if _selected_id != -1 and _ground.has(army_id) and not is_own:
			order_selected_to(_ground[army_id])
		else:
			select(army_id)
	elif event.button_index == MOUSE_BUTTON_RIGHT and _ground.has(army_id):
		# Right-clicking any piece is an attack order: march onto it and let
		# the arrival resolve into a field battle.
		order_selected_to(_ground[army_id])


## Selects an army. Enemy pieces are inspectable but not commandable, so
## selecting one clears the selection instead.
func select(army_id: int) -> void:
	if army_id != -1 and int(_army_field(army_id, "owner")) != PLAYER_FACTION:
		army_id = -1
	_selected_id = army_id
	sync(_manager.get_state())
	army_selected.emit(_selected_id)


## Turns a screen click into a march order for the selected army.
func order_selected_at_screen(screen_position: Vector2) -> void:
	var ground: Vector2 = _screen_to_world.call(screen_position)
	if ground != Vector2.INF:
		order_selected_to(ground)


func order_selected_to(ground: Vector2) -> void:
	if _selected_id == -1 or _orders_locked:
		return
	if _manager.current_faction_id() != PLAYER_FACTION or _manager.is_game_over():
		push_warning(
			(
				"order_selected_to rejected: current_faction_id=%d, PLAYER_FACTION=%d, game_over=%s"
				% [_manager.current_faction_id(), PLAYER_FACTION, _manager.is_game_over()]
			)
		)
		return
	if not _manager.move_army(_selected_id, ground.x, ground.y):
		# The engine rejected the order (e.g. the target isn't one of this
		# army's reachable provinces this turn) - deselect instead of leaving
		# the army armed with no feedback that the click did nothing.
		select(-1)
		return
	# An army that finished its march inside one of our own empty cities takes
	# up residence there rather than camping outside the gate.
	var entered: int = _manager.garrison_army(_selected_id)
	if entered != -1:
		log_message.emit("Army garrisons in city %d." % entered)
	state_changed.emit()


## ---------------------------------------------------------------------------
## Animation
## ---------------------------------------------------------------------------


## Slides a piece from `from` to `to`. Fires for player- and AI-ordered moves
## alike, since both come back through the same signal.
func _on_army_moved(
	army_id: int, from: Vector2, to: Vector2, _spent: float, _movement_left: float
) -> void:
	if _tweens.has(army_id):
		var running: Tween = _tweens[army_id]
		if is_instance_valid(running):
			running.kill()
	_ground[army_id] = from

	var tween := create_tween()
	tween.set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	tween.tween_method(_set_ground.bind(army_id), from, to, MOVE_SECONDS)
	tween.finished.connect(_on_tween_finished.bind(army_id))
	_tweens[army_id] = tween


func _set_ground(ground: Vector2, army_id: int) -> void:
	_ground[army_id] = ground
	project()


func _on_tween_finished(army_id: int) -> void:
	_tweens.erase(army_id)


func _on_army_battle(report: Dictionary) -> void:
	var attacker: int = int(report["attacker_faction"])
	var defender: int = int(report["defender_faction"])
	var won: bool = bool(report["attacker_won"])

	var msg := ""
	if String(report["kind"]) == "siege":
		var verb := "stormed" if won else "was thrown back from"
		msg = (
			"Faction %d %s city %d (faction %d)."
			% [attacker, verb, int(report["city_id"]), defender]
		)
	else:
		var verb := "beat" if won else "lost to"
		msg = "Faction %d's army %s faction %d's in the field." % [attacker, verb, defender]
	if bool(report["defender_eliminated"]):
		msg += " Faction %d is eliminated!" % defender
	log_message.emit(msg)

	# The model resolved this the instant the order was given, but the piece is
	# still visibly marching. Let the animation land before the loser vanishes.
	var loser: int = int(report["loser_army"])
	if loser != -1:
		await get_tree().create_timer(MOVE_SECONDS).timeout
		_remove_marker(loser)
