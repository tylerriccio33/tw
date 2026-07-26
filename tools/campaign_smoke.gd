extends SceneTree

# Smoke test: does the campaign GDExtension load, and does a full game
# actually play to completion through its public API? Run with:
#   godot --headless --script res://tools/campaign_smoke.gd
#
# Counters live on `self` rather than in lambda closures: GDScript lambdas
# capture locals by value, not by reference, so `func(): x += 1` never
# mutates an outer local.

var turn_started_count := 0
var battle_count := 0
var game_over_fired := false


func _on_turn_started(_faction_id: int, _turn: int) -> void:
	turn_started_count += 1


func _on_battle_resolved(_a: int, _d: int, _c: int, _won: bool, _elim: bool) -> void:
	battle_count += 1


func _on_game_over(_winner: int) -> void:
	game_over_fired = true


func _initialize() -> void:
	var failures: Array[String] = []

	if not ClassDB.class_exists("CampaignManager"):
		failures.append("class not registered: CampaignManager")
		_finish(failures)
		return

	var manager: Object = ClassDB.instantiate("CampaignManager")
	if manager == null:
		failures.append("CampaignManager failed to instantiate")
		_finish(failures)
		return

	manager.turn_started.connect(_on_turn_started)
	manager.battle_resolved.connect(_on_battle_resolved)
	manager.game_over.connect(_on_game_over)

	manager.start_default_game()
	var state: Dictionary = manager.get_state()
	if state["factions"].size() != 4:
		failures.append("expected 4 factions, got %d" % state["factions"].size())
	if state["cities"].size() != 4:
		failures.append("expected 4 cities, got %d" % state["cities"].size())

	# Play until the game ends or a safety cap trips, alternating attacks and
	# passes so both attack_city() and end_turn() get exercised.
	var iterations := 0
	while not manager.is_game_over() and iterations < 500:
		var s: Dictionary = manager.get_state()
		var current: int = s["current_faction"]
		var target_id: int = -1
		for city in s["cities"]:
			if int(city["owner"]) != current:
				target_id = int(city["id"])
				break
		if target_id != -1:
			manager.attack_city(target_id)
		manager.end_turn()
		iterations += 1

	if not manager.is_game_over():
		failures.append("game did not end within %d iterations" % iterations)
	if turn_started_count == 0:
		failures.append("turn_started never fired")
	if battle_count == 0:
		failures.append("battle_resolved never fired")
	if not game_over_fired:
		failures.append("game_over never fired")
	if manager.winner_id() < 0:
		failures.append("winner_id is negative after game over")

	print(
		(
			"campaign smoke: turns=%d battles=%d winner=%d"
			% [turn_started_count, battle_count, manager.winner_id()]
		)
	)

	manager.free()
	_finish(failures)


func _finish(failures: Array[String]) -> void:
	if failures.is_empty():
		print("CAMPAIGN SMOKE OK")
		quit(0)
	else:
		for f in failures:
			printerr("CAMPAIGN SMOKE FAIL: ", f)
		quit(1)
