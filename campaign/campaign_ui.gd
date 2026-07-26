extends Node3D
## Thin GDScript glue: builds the real 3D world from config/world.json, sets
## up a static top-down camera over it, and drives the Rust CampaignManager
## with the world's real city positions instead of a hardcoded square. City
## markers/dropdown/end-turn button render state on every signal instead of
## polling - unchanged from before this script also owned world/camera setup.

const WorldBuilder := preload("res://world/world_builder.gd")
const WORLD_CONFIG := "res://config/world.json"
const MAX_TURNS := 10

const FACTION_COLORS: Array[Color] = [
	Color.INDIAN_RED, Color.CORNFLOWER_BLUE, Color.MEDIUM_SEA_GREEN, Color.GOLDENROD
]

@onready var manager: Node = $CampaignManager
@onready var status_label: Label = $UI/StatusLabel
@onready var log_label: RichTextLabel = $UI/LogLabel
@onready var target_option: OptionButton = $UI/Controls/TargetOption
@onready var attack_button: Button = $UI/Controls/AttackButton
@onready var end_turn_button: Button = $UI/Controls/EndTurnButton
@onready var cities_root: Control = $CityMarkers

var city_markers: Dictionary = {}

var _world_camera: Camera3D
var _city_centres: PackedVector3Array = []
var _marker_positions: Dictionary = {}


func _load_world_config() -> Dictionary:
	if not FileAccess.file_exists(WORLD_CONFIG):
		return {}
	var text := FileAccess.get_file_as_string(WORLD_CONFIG)
	if text.is_empty():
		return {}
	var json := JSON.new()
	if json.parse(text) != OK or typeof(json.data) != TYPE_DICTIONARY:
		return {}
	return json.data


func _fail_to_start(message: String) -> void:
	printerr("error: ", message)
	status_label.text = message
	attack_button.disabled = true
	end_turn_button.disabled = true


func _ready() -> void:
	var cfg := _load_world_config()
	if cfg.is_empty():
		_fail_to_start("could not load %s" % WORLD_CONFIG)
		return

	var world := WorldBuilder.new()
	world.name = "World"
	add_child(world)
	world.build(cfg)
	if not world.built or not world.errors.is_empty():
		_fail_to_start("world build failed: %s" % ", ".join(world.errors))
		return

	_world_camera = Camera3D.new()
	_world_camera.name = "WorldCamera"
	_world_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	_world_camera.near = 1.0
	_world_camera.far = 20000.0
	var map_extent: float = world.terrain_builder.map_extent
	_world_camera.size = map_extent * 2.0 * 1.05
	add_child(_world_camera)
	_world_camera.global_position = Vector3(0.0, 4000.0, 0.01)
	_world_camera.look_at(Vector3.ZERO, Vector3.FORWARD)
	_world_camera.make_current()

	world.set_camera(_world_camera)
	if not world.errors.is_empty():
		_fail_to_start("set_camera failed: %s" % ", ".join(world.errors))
		return

	_city_centres = world.city_centres()
	if _city_centres.is_empty():
		_fail_to_start("world built with 0 cities - nothing to play")
		return

	var positions := PackedVector2Array()
	for c in _city_centres:
		positions.append(Vector2(c.x, c.z))

	manager.turn_started.connect(_on_turn_started)
	manager.battle_resolved.connect(_on_battle_resolved)
	manager.game_over.connect(_on_game_over)
	attack_button.pressed.connect(_on_attack_pressed)
	end_turn_button.pressed.connect(_on_end_turn_pressed)

	# start_game_from_positions() emits turn_started synchronously, so
	# _refresh() can run before this function returns - city markers must
	# already exist by then. _ensure_city_marker() below makes marker
	# creation lazy so there is no ordering requirement between the two.
	manager.start_game_from_positions(positions, MAX_TURNS)
	_project_markers()
	_refresh()
	get_viewport().size_changed.connect(_project_markers)


## Screen position of every city, from its real 3D centre through the static
## top-down camera. Computed once (plus on resize) since the camera never
## moves - re-running every frame would be wasted work.
func _project_markers() -> void:
	for i in _city_centres.size():
		var pos := _world_camera.unproject_position(_city_centres[i])
		_marker_positions[i] = pos
		if city_markers.has(i):
			var marker: ColorRect = city_markers[i]
			marker.position = pos - marker.size / 2.0


func _ensure_city_marker(city: Dictionary) -> ColorRect:
	var id: int = int(city["id"])
	if city_markers.has(id):
		return city_markers[id]

	var pos: Vector2 = _marker_positions.get(id, Vector2.ZERO)

	var marker := ColorRect.new()
	marker.size = Vector2(32, 32)
	marker.position = pos - marker.size / 2.0
	cities_root.add_child(marker)
	city_markers[id] = marker

	var label := Label.new()
	label.text = city["name"]
	label.position = pos + Vector2(-16, 18)
	cities_root.add_child(label)

	return marker


func _refresh() -> void:
	var state: Dictionary = manager.get_state()

	for city in state["cities"]:
		var marker: ColorRect = _ensure_city_marker(city)
		marker.color = FACTION_COLORS[int(city["owner"]) % FACTION_COLORS.size()]

	var lines: Array[String] = []
	lines.append("Turn %d / %d" % [state["turn"], state["max_turns"]])
	for faction in state["factions"]:
		var alive_marker := "" if faction["alive"] else " (eliminated)"
		lines.append(
			(
				"%s: $%d, %d cities%s"
				% [faction["name"], faction["money"], faction["cities"], alive_marker]
			)
		)
	status_label.text = "\n".join(lines)

	_rebuild_target_options(state)

	if state["game_over"]:
		attack_button.disabled = true
		end_turn_button.disabled = true
		var winner_id: int = state["winner"]
		var winner_name := "nobody"
		for faction in state["factions"]:
			if int(faction["id"]) == winner_id:
				winner_name = faction["name"]
		_append_log("[b]Game over. %s wins.[/b]" % winner_name)


func _rebuild_target_options(state: Dictionary) -> void:
	target_option.clear()
	var current_faction: int = state["current_faction"]
	for city in state["cities"]:
		if int(city["owner"]) != current_faction:
			target_option.add_item(
				"%s (owned by faction %d)" % [city["name"], city["owner"]], int(city["id"])
			)
	attack_button.disabled = target_option.item_count == 0 or state["game_over"]


func _on_attack_pressed() -> void:
	if target_option.item_count == 0:
		return
	var target_city_id: int = target_option.get_item_id(target_option.selected)
	manager.attack_city(target_city_id)


func _on_end_turn_pressed() -> void:
	manager.end_turn()


func _on_turn_started(faction_id: int, turn: int) -> void:
	_append_log("Turn %d: faction %d begins." % [turn, faction_id])
	_refresh()


func _on_battle_resolved(
	attacker_id: int, defender_id: int, city_id: int, attacker_won: bool, defender_eliminated: bool
) -> void:
	var verb := "captured" if attacker_won else "failed to capture"
	var msg := "Faction %d %s city %d from faction %d." % [attacker_id, verb, city_id, defender_id]
	if defender_eliminated:
		msg += " Faction %d is eliminated!" % defender_id
	_append_log(msg)
	_refresh()


func _on_game_over(_winner_id: int) -> void:
	_refresh()


func _append_log(text: String) -> void:
	log_label.append_text(text + "\n")
