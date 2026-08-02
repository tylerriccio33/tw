extends Node
## Owns the post-battle presentation: queues clash animations + a blocking
## result modal off campaign_ui's army_battle handling, so campaign_ui.gd
## doesn't have to carry all of it inline. Holds a reference to campaign_ui
## itself (duck-typed) for the manager/army_layer/_world_to_screen/_refresh it
## needs, and mirrors its HUD_BLUE_DARK/HUD_CREAM/_battle_modal* fields so
## campaign_hud_builder.gd's build_battle_modal() works unchanged against an
## instance of this controller.

const BattleAnimation := preload("res://campaign/battle_animation.gd")
const HudBuilder := preload("res://campaign/campaign_hud_builder.gd")

const HUD_BLUE_DARK := Color(0.11, 0.15, 0.22)
const HUD_CREAM := Color(0.95, 0.94, 0.88)

var _owner: Node2D  # campaign_ui.gd, duck-typed for the fields/methods used below
var _ui: Control  # campaign_ui.gd's $UI container, where the modal gets added
var _last_move_to := Vector2.ZERO
var _battle_queue: Array[Dictionary] = []
var _battle_pending := false
var _processing_battles := false
var _battle_modal: Control
var _battle_modal_title_label: Label
var _battle_modal_message_label: Label
var _battle_modal_ok_button: Button


func setup(campaign_ui: Node2D) -> void:
	_owner = campaign_ui
	_ui = campaign_ui._ui
	HudBuilder.build_battle_modal(self)


func _faction_name(faction_id: int) -> String:
	for faction in _owner.manager.get_state()["factions"]:
		if int(faction["id"]) == faction_id:
			return String(faction["name"])
	return "Faction %d" % faction_id


func is_pending() -> bool:
	return _battle_pending


## Tracks the destination of the most recent army_moved signal, regardless of
## mover or cause - army_battle always fires immediately after the army_moved
## for the same order (see godot_api.rs::emit_move), so this is the battle's
## world position by the time on_army_battle reads it.
func on_army_moved(
	_army_id: int, _from: Vector2, to: Vector2, _spent: float, _movement_left: float
) -> void:
	_last_move_to = to


## Queues a battle's clash animation + result modal. Multiple battles can
## stack up within a single manager.run_ai_turn() call (it moves every army a
## faction owns before GDScript gets control back), so this only starts the
## processing coroutine once; further battles append to the queue it drains.
func on_army_battle(report: Dictionary) -> void:
	_battle_queue.append({"report": report, "position": _last_move_to})
	if not _processing_battles:
		_process_queue()


## Drains _battle_queue one battle at a time: plays the clash animation at
## the battle's location, then shows the result modal and awaits the player's
## OK. Orders and END TURN stay locked (_battle_pending) for the whole
## drain, so nothing can advance past an unacknowledged battle. Restores
## army_layer's order lock to whatever is_ai_running wants once done, since an
## AI turn may still be in progress after the last queued battle.
func _process_queue() -> void:
	_processing_battles = true
	_battle_pending = true
	_owner._army_layer.set_orders_locked(true)
	_owner._refresh()
	while not _battle_queue.is_empty():
		var entry: Dictionary = _battle_queue.pop_front()
		await _play_sequence(entry["report"], entry["position"])
	_processing_battles = false
	_battle_pending = false
	_owner._army_layer.set_orders_locked(_owner._ai_running)
	_owner._refresh()


func _play_sequence(report: Dictionary, world_pos: Vector2) -> void:
	var fx := BattleAnimation.new()
	add_child(fx)
	fx.position = _owner._world_to_screen(world_pos)
	await fx.finished
	await _show_result_modal(report)


## Builds the winner-announcement text, shows the modal built by
## HudBuilder.build_battle_modal(), and blocks until the player clicks OK.
func _show_result_modal(report: Dictionary) -> void:
	var attacker: int = int(report["attacker_faction"])
	var defender: int = int(report["defender_faction"])
	var won: bool = bool(report["attacker_won"])
	var winner_id := attacker if won else defender
	var winner_name: String = _faction_name(winner_id)

	_battle_modal_title_label.text = "%s wins the battle!" % winner_name

	var msg := ""
	if String(report["kind"]) == "siege":
		var verb := "stormed" if won else "was repelled from"
		msg = "%s %s city %d." % [_faction_name(attacker), verb, int(report["city_id"])]
	else:
		var verb := "defeated" if won else "was defeated by"
		msg = (
			"%s's army %s %s's in the field."
			% [_faction_name(attacker), verb, _faction_name(defender)]
		)
	if bool(report["defender_eliminated"]):
		msg += " %s has been eliminated!" % _faction_name(defender)
	_battle_modal_message_label.text = msg

	_battle_modal.visible = true
	await _battle_modal_ok_button.pressed
	_battle_modal.visible = false


## Waits until the battle queue (and whatever it's currently presenting) is
## fully drained - used by _run_ai_factions so an AI turn can't end_turn()
## past a battle the player hasn't acknowledged yet.
func wait_for_queue() -> void:
	while _battle_pending or not _battle_queue.is_empty():
		await get_tree().process_frame
