extends Node2D
## Thin GDScript glue for the Rust CampaignManager: wires up city markers,
## an attack-target dropdown, and an end-turn button; renders state on every
## signal instead of polling.

const FACTION_COLORS: Array[Color] = [
	Color.INDIAN_RED, Color.CORNFLOWER_BLUE, Color.MEDIUM_SEA_GREEN, Color.GOLDENROD
]

@onready var manager: Node = $CampaignManager
@onready var status_label: Label = $UI/StatusLabel
@onready var log_label: RichTextLabel = $UI/LogLabel
@onready var target_option: OptionButton = $UI/Controls/TargetOption
@onready var attack_button: Button = $UI/Controls/AttackButton
@onready var end_turn_button: Button = $UI/Controls/EndTurnButton
@onready var cities_root: Node2D = $Cities

var city_markers: Dictionary = {}


func _ready() -> void:
	manager.turn_started.connect(_on_turn_started)
	manager.battle_resolved.connect(_on_battle_resolved)
	manager.game_over.connect(_on_game_over)
	attack_button.pressed.connect(_on_attack_pressed)
	end_turn_button.pressed.connect(_on_end_turn_pressed)

	# start_default_game() emits turn_started synchronously, so _refresh() can
	# run before this function returns - city markers must already exist by
	# then. _ensure_city_marker() below makes marker creation lazy so there is
	# no ordering requirement between the two.
	manager.start_default_game()
	_refresh()


func _ensure_city_marker(city: Dictionary) -> ColorRect:
	var id: int = int(city["id"])
	if city_markers.has(id):
		return city_markers[id]

	var marker := ColorRect.new()
	marker.size = Vector2(32, 32)
	marker.position = Vector2(city["x"], city["y"]) - marker.size / 2.0
	cities_root.add_child(marker)
	city_markers[id] = marker

	var label := Label.new()
	label.text = city["name"]
	label.position = Vector2(city["x"], city["y"]) + Vector2(-16, 18)
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
