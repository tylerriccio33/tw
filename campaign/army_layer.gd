extends Control
## Army pieces on the campaign map: draws one marker per living army, animates
## them along the moves the Rust model reports, and turns player clicks into
## move orders.
##
## Movement is continuous rather than tile-based. An army has a pool of move
## points and spends one per world unit of straight-line distance, with no
## terrain modifier at all - mountains, roads and open ocean all cost the same,
## and an order past the pool marches as far as it reaches along that heading.
## Every rule of that lives in Rust (`Campaign::move_army`); this script only
## draws pieces and forwards orders.
##
## The parent (campaign_ui.gd) owns the camera, the terrain and the manager and
## hands them over in setup(); this node reports back through `log_message` and
## `state_changed` rather than reaching up into its parent.

signal log_message(text: String)
## Emitted when an order this node issued changed the campaign, so the parent
## can re-render the rest of the HUD.
signal state_changed

const PLAYER_FACTION := 0
const MARKER_SIZE := Vector2(24, 24)
const LIFT := 14.0  # world units above the terrain, so a piece isn't buried in a slope
const MOVE_SECONDS := 0.7  # how long one army's move animation takes
const RANGE_RING_POINTS := 48
const RANGE_RING_COLOR := Color(1.0, 1.0, 1.0, 0.55)

var _manager: Node
var _camera: Camera3D
var _terrain: Node
var _faction_colors: Array[Color] = []

var _range_ring: Line2D
var _markers: Dictionary = {}
## Animated source of truth for where each piece is *drawn*, as world (x, z).
## The tween writes into this and project() turns it into screen space, so
## panning or zooming mid-animation stays correct. It deliberately lags the
## model, which teleports an army the instant its order resolves.
var _ground: Dictionary = {}
var _tweens: Dictionary = {}
var _selected_id: int = -1
var _orders_locked := false


func setup(manager: Node, camera: Camera3D, terrain: Node, faction_colors: Array[Color]) -> void:
	_manager = manager
	_camera = camera
	_terrain = terrain
	_faction_colors = faction_colors

	set_anchors_preset(Control.PRESET_FULL_RECT)
	# IGNORE on the container only - the markers themselves still take clicks,
	# and anything that misses one falls through to the parent's
	# _unhandled_input, which is where move orders and deselection land.
	mouse_filter = Control.MOUSE_FILTER_IGNORE

	# Added first so it draws underneath the pieces.
	_range_ring = Line2D.new()
	_range_ring.name = "RangeRing"
	_range_ring.width = 2.0
	_range_ring.default_color = RANGE_RING_COLOR
	_range_ring.closed = true
	_range_ring.visible = false
	add_child(_range_ring)

	_manager.army_moved.connect(_on_army_moved)
	_manager.army_battle.connect(_on_army_battle)


## Blocks player orders while the AI is playing its turns.
func set_orders_locked(locked: bool) -> void:
	_orders_locked = locked


func selected_army_id() -> int:
	return _selected_id


## ---------------------------------------------------------------------------
## Geometry
## ---------------------------------------------------------------------------


## World-space point for a campaign (x, z) coordinate, lifted clear of the
## ground so a piece on a slope still reads as standing on top of it.
func _world_pos(ground: Vector2) -> Vector3:
	var height := 0.0
	if _terrain != null:
		var sampled: float = _terrain.height_at(ground.x, ground.y)
		if is_finite(sampled):
			height = sampled
	return Vector3(ground.x, height + LIFT, ground.y)


## Inverse of the above: which campaign (x, z) a screen pixel points at, found
## by intersecting the camera ray with the y=0 sea-level plane. Returns
## Vector2.INF when the ray runs parallel to the plane or points at the sky.
func _ground_point(screen: Vector2) -> Vector2:
	var origin := _camera.project_ray_origin(screen)
	var direction := _camera.project_ray_normal(screen)
	if absf(direction.y) < 0.0001:
		return Vector2.INF
	var t := -origin.y / direction.y
	if t <= 0.0:
		return Vector2.INF
	var hit := origin + direction * t
	return Vector2(hit.x, hit.z)


## ---------------------------------------------------------------------------
## Markers
## ---------------------------------------------------------------------------


func _ensure_marker(army: Dictionary) -> Panel:
	var id: int = int(army["id"])
	if _markers.has(id):
		return _markers[id]

	var marker := Panel.new()
	marker.size = MARKER_SIZE
	marker.mouse_filter = Control.MOUSE_FILTER_STOP
	marker.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	marker.gui_input.connect(_on_marker_input.bind(id))
	add_child(marker)
	_markers[id] = marker
	return marker


func _marker_style(owner_id: int, selected: bool) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = _faction_colors[owner_id % _faction_colors.size()]
	sb.set_border_width_all(3 if selected else 2)
	sb.border_color = Color.WHITE if selected else Color(0.0, 0.0, 0.0, 0.7)
	# Round it right off into a disc, so armies never read as city squares.
	var radius := int(MARKER_SIZE.x / 2.0)
	sb.corner_radius_top_left = radius
	sb.corner_radius_top_right = radius
	sb.corner_radius_bottom_left = radius
	sb.corner_radius_bottom_right = radius
	return sb


## Rebuilds the pieces from a state snapshot: creates any new marker, restyles
## the rest, and drops the ones whose army died. Armies that are mid-animation
## keep their drawn position, so a refresh can't teleport a piece that is still
## visibly marching.
func sync(state: Dictionary) -> void:
	var live: Dictionary = {}
	for army in state.get("armies", []):
		var id: int = int(army["id"])
		live[id] = true
		var marker := _ensure_marker(army)
		marker.add_theme_stylebox_override(
			"panel", _marker_style(int(army["owner"]), id == _selected_id)
		)
		marker.tooltip_text = (
			"%s\n%d / %d move points%s"
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


func _remove_marker(id: int) -> void:
	if _tweens.has(id):
		var tween: Tween = _tweens[id]
		if is_instance_valid(tween):
			tween.kill()
		_tweens.erase(id)
	if _markers.has(id):
		var marker: Panel = _markers[id]
		marker.queue_free()
		_markers.erase(id)
	_ground.erase(id)


## Screen-projects every piece and the selection ring from the current camera.
## Called on any camera change, and every frame while a piece is animating.
func project() -> void:
	for id: int in _markers.keys():
		var marker: Panel = _markers[id]
		var world := _world_pos(_ground[id])
		if _camera.is_position_behind(world):
			marker.visible = false
			continue
		marker.visible = true
		marker.position = _camera.unproject_position(world) - marker.size / 2.0
	_project_range_ring()


## The selected army's remaining reach, as a world-space circle projected point
## by point. Projecting the circle rather than drawing one on screen keeps it an
## honest ellipse under the tilted camera, so what it covers really is where the
## army can reach this turn.
func _project_range_ring() -> void:
	var radius := _army_field(_selected_id, "movement")
	if _selected_id == -1 or not _ground.has(_selected_id) or radius <= 0.0:
		_range_ring.visible = false
		return

	var centre: Vector2 = _ground[_selected_id]
	var points := PackedVector2Array()
	for i in RANGE_RING_POINTS:
		var angle := TAU * float(i) / float(RANGE_RING_POINTS)
		var world := _world_pos(centre + Vector2(cos(angle), sin(angle)) * radius)
		if _camera.is_position_behind(world):
			_range_ring.visible = false
			return
		points.append(_camera.unproject_position(world))
	_range_ring.points = points
	_range_ring.visible = true


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
		select(army_id)
	elif event.button_index == MOUSE_BUTTON_RIGHT and _ground.has(army_id):
		# Right-clicking an enemy piece is an attack order: march onto it and
		# let the arrival resolve into a field battle.
		order_selected_to(_ground[army_id])


## Selects an army. Enemy pieces are inspectable but not commandable, so
## selecting one clears the selection instead.
func select(army_id: int) -> void:
	if army_id != -1 and int(_army_field(army_id, "owner")) != PLAYER_FACTION:
		army_id = -1
	_selected_id = army_id
	sync(_manager.get_state())


## Turns a screen click into a march order for the selected army.
func order_selected_at_screen(screen_position: Vector2) -> void:
	var ground := _ground_point(screen_position)
	if ground != Vector2.INF:
		order_selected_to(ground)


func order_selected_to(ground: Vector2) -> void:
	if _selected_id == -1 or _orders_locked:
		return
	if _manager.current_faction_id() != PLAYER_FACTION or _manager.is_game_over():
		return
	if not _manager.move_army(_selected_id, ground.x, ground.y):
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
