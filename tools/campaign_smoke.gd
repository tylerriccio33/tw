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
var army_moved_count := 0
var army_battle_count := 0


func _on_army_moved(_id: int, _from: Vector2, _to: Vector2, _spent: float, _left: float) -> void:
	army_moved_count += 1


func _on_army_battle(_report: Dictionary) -> void:
	army_battle_count += 1


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

	# Exercise the world-derived start path with a non-default city count,
	# since start_default_game() alone would never catch a regression in
	# start_game_from_positions()'s faction-clamping/round-robin ownership.
	var manager2: Object = ClassDB.instantiate("CampaignManager")
	if manager2 == null:
		failures.append("CampaignManager failed to instantiate (second instance)")
		_finish(failures)
		return

	turn_started_count = 0
	battle_count = 0
	game_over_fired = false
	manager2.turn_started.connect(_on_turn_started)
	manager2.battle_resolved.connect(_on_battle_resolved)
	manager2.game_over.connect(_on_game_over)

	var positions := PackedVector2Array(
		[
			Vector2(0, 0),
			Vector2(100, 0),
			Vector2(0, 100),
			Vector2(100, 100),
			Vector2(200, 200),
			Vector2(200, 0)
		]
	)
	manager2.start_game_from_positions(positions, 10)
	var state2: Dictionary = manager2.get_state()
	if state2["factions"].size() != 4:
		failures.append(
			(
				"start_game_from_positions: expected 4 factions (clamped), got %d"
				% state2["factions"].size()
			)
		)
	if state2["cities"].size() != 6:
		failures.append(
			"start_game_from_positions: expected 6 cities, got %d" % state2["cities"].size()
		)

	iterations = 0
	while not manager2.is_game_over() and iterations < 500:
		var s2: Dictionary = manager2.get_state()
		var current2: int = s2["current_faction"]
		var target_id2: int = -1
		for city in s2["cities"]:
			if int(city["owner"]) != current2:
				target_id2 = int(city["id"])
				break
		if target_id2 != -1:
			manager2.attack_city(target_id2)
		manager2.end_turn()
		iterations += 1

	if not manager2.is_game_over():
		failures.append(
			"start_game_from_positions: game did not end within %d iterations" % iterations
		)
	if turn_started_count == 0:
		failures.append("start_game_from_positions: turn_started never fired")
	if battle_count == 0:
		failures.append("start_game_from_positions: battle_resolved never fired")
	if not game_over_fired:
		failures.append("start_game_from_positions: game_over never fired")

	print(
		(
			"campaign smoke (world-derived): turns=%d battles=%d winner=%d"
			% [turn_started_count, battle_count, manager2.winner_id()]
		)
	)

	manager2.free()

	failures.append_array(_check_armies())
	_finish(failures)


## Exercises the army half of the API on a fresh game: every faction should
## start with one garrisoned army, a player-issued move should spend points and
## emit army_moved, an over-long order should still move (capped at the pool),
## and a few AI turns should keep armies inside the map and eventually fight.
func _check_armies() -> Array[String]:
	var failures: Array[String] = []

	var manager: Object = ClassDB.instantiate("CampaignManager")
	if manager == null:
		return ["armies: CampaignManager failed to instantiate"]

	army_moved_count = 0
	army_battle_count = 0
	manager.army_moved.connect(_on_army_moved)
	manager.army_battle.connect(_on_army_battle)

	manager.set_map_extent(2048.0)
	manager.start_default_game()

	var state: Dictionary = manager.get_state()
	if state["armies"].size() != 4:
		failures.append("armies: expected one army per faction, got %d" % state["armies"].size())
	for army in state["armies"]:
		if int(army["garrisoned"]) == -1:
			failures.append("armies: army %d should start garrisoned" % int(army["id"]))
		if float(army["movement"]) != float(army["max_movement"]):
			failures.append("armies: army %d should start with a full move pool" % int(army["id"]))

	# The first faction's army marches somewhere harmless and should spend
	# exactly the distance it covered.
	var first: Dictionary = state["armies"][0]
	var move_points: float = float(first["max_movement"])
	var start := Vector2(float(first["x"]), float(first["y"]))
	if not manager.move_army(int(first["id"]), start.x, start.y - 50.0):
		failures.append("armies: a short move order was rejected")
	var after: Dictionary = _find_army(manager.get_state(), int(first["id"]))
	if after.is_empty():
		failures.append("armies: army vanished after a harmless move")
	elif not is_equal_approx(float(after["movement"]), move_points - 50.0):
		failures.append(
			(
				"armies: a 50-unit move should cost 50 points, left %.2f of %.2f"
				% [float(after["movement"]), move_points]
			)
		)
	if army_moved_count == 0:
		failures.append("armies: army_moved never fired")

	# Ordering past the remaining pool must still move, capped, rather than
	# failing outright.
	if not manager.move_army(int(first["id"]), start.x, start.y - 99999.0):
		failures.append("armies: an over-long move order should be capped, not rejected")
	after = _find_army(manager.get_state(), int(first["id"]))
	if not after.is_empty() and float(after["movement"]) > 0.001:
		failures.append("armies: an over-long move should drain the whole pool")

	# Let the random AI run the rest of the campaign out.
	manager.end_turn()
	var iterations := 0
	while not manager.is_game_over() and iterations < 500:
		manager.run_ai_turn()
		manager.end_turn()
		iterations += 1

	var extent: float = float(manager.get_state()["map_extent"])
	for army in manager.get_state()["armies"]:
		if absf(float(army["x"])) > extent + 0.001 or absf(float(army["y"])) > extent + 0.001:
			failures.append(
				(
					"armies: army %d wandered off the map to (%.1f, %.1f)"
					% [int(army["id"]), float(army["x"]), float(army["y"])]
				)
			)

	print(
		(
			"campaign smoke (armies): moves=%d battles=%d ai_turns=%d"
			% [army_moved_count, army_battle_count, iterations]
		)
	)

	manager.free()
	return failures


func _find_army(state: Dictionary, army_id: int) -> Dictionary:
	for army in state["armies"]:
		if int(army["id"]) == army_id:
			return army
	return {}


func _finish(failures: Array[String]) -> void:
	if failures.is_empty():
		print("CAMPAIGN SMOKE OK")
		quit(0)
	else:
		for f in failures:
			printerr("CAMPAIGN SMOKE FAIL: ", f)
		quit(1)
