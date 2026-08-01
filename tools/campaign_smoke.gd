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


## The map package's faction roster, as factions.json ships it.
func _sample_factions() -> Array:
	return [
		{"key": "red", "name": "Red", "color": "#cd5c5c", "money": 100},
		{"key": "blue", "name": "Blue", "color": "#6495ed", "money": 100},
		{"key": "green", "name": "Green", "color": "#3cb371", "money": 100},
		{"key": "yellow", "name": "Yellow", "color": "#daa520", "money": 100},
	]


## A province table shaped like provinces.table.json: one province per
## position, owners round-robin across the roster, each bordering the next so
## the adjacency graph is a connected ring.
##
## `city_position` is offset from `centroid` on purpose, so a test can tell
## whether Rust actually sited the city on the authored point rather than
## silently falling back to the area centroid.
func _sample_provinces(positions: PackedVector2Array) -> Array:
	var keys := ["red", "blue", "green", "yellow"]
	var rows: Array = []
	for i in positions.size():
		var prev: int = ((i - 1) + positions.size()) % positions.size() + 1
		var next: int = (i + 1) % positions.size() + 1
		(
			rows
			. append(
				{
					"id": i + 1,
					"key": "province_%d" % (i + 1),
					"name": "Province %d" % (i + 1),
					"centroid": [positions[i].x, positions[i].y],
					"city_position": [positions[i].x + 5, positions[i].y + 5],
					"area_px": 1000,
					"neighbors": [prev, next],
					"starting_owner": keys[i % keys.size()],
					"tags": {"terrain": "plains", "resources": ["iron"]},
				}
			)
		)
	return rows


## A province table for _check_armies only: a 1-2-3-4 chain all owned by Red
## (so hops between them never risk a coin-flip siege), plus three isolated,
## neighborless provinces - one each for Blue/Green/Yellow, just so every
## faction still gets the one starting army spawn_starting_armies gives
## whoever owns a city, without those armies bordering Red's chain.
func _army_test_provinces() -> Array:
	var rows: Array = []
	var chain_positions := _default_positions()
	for i in chain_positions.size():
		var neighbors: Array = []
		if i > 0:
			neighbors.append(i)
		if i < chain_positions.size() - 1:
			neighbors.append(i + 2)
		(
			rows
			. append(
				{
					"id": i + 1,
					"key": "province_%d" % (i + 1),
					"name": "Province %d" % (i + 1),
					"centroid": [chain_positions[i].x, chain_positions[i].y],
					"city_position": [chain_positions[i].x + 5, chain_positions[i].y + 5],
					"area_px": 1000,
					"neighbors": neighbors,
					"starting_owner": "red",
					"tags": {"terrain": "plains", "resources": ["iron"]},
				}
			)
		)

	var outsiders := ["blue", "green", "yellow"]
	for i in outsiders.size():
		var n := chain_positions.size() + i
		(
			rows
			. append(
				{
					"id": n + 1,
					"key": "province_%d" % (n + 1),
					"name": "Province %d" % (n + 1),
					"centroid": [1000.0 + n * 100.0, 1000.0],
					"city_position": [1005.0 + n * 100.0, 1005.0],
					"area_px": 1000,
					"neighbors": [],
					"starting_owner": outsiders[i],
					"tags": {"terrain": "plains", "resources": ["iron"]},
				}
			)
		)
	return rows


func _start_from_provinces(manager: Object, positions: PackedVector2Array, max_turns: int) -> void:
	manager.load_factions(_sample_factions())
	manager.load_provinces(_sample_provinces(positions))
	manager.start_game_from_provinces(max_turns)


## Four corners, one province per faction. Not a const: PackedVector2Array
## literals aren't constant expressions in GDScript.
func _default_positions() -> PackedVector2Array:
	return PackedVector2Array(
		[Vector2(-200, -200), Vector2(200, -200), Vector2(-200, 200), Vector2(200, 200)]
	)


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

	_start_from_provinces(manager, _default_positions(), 10)
	var state: Dictionary = manager.get_state()

	# The province table has to survive the trip into Rust and back out, or
	# the map has no way to colour itself.
	if state["provinces"].size() != 4:
		failures.append("expected 4 provinces, got %d" % state["provinces"].size())
	for province in state["provinces"]:
		if int(province["owner"]) < 0:
			failures.append("province %d has no starting owner" % int(province["id"]))
		if String(province["tags"].get("terrain", "")) != "plains":
			failures.append("province %d lost its terrain tag" % int(province["id"]))
		if province["tags"].get("resources", []) != ["iron"]:
			failures.append("province %d lost its resources tag" % int(province["id"]))
	for faction in state["factions"]:
		if not String(faction.get("color", "")).begins_with("#"):
			failures.append(
				"faction %d has no color for the map to paint with" % int(faction["id"])
			)

	if state["factions"].size() != 4:
		failures.append("expected 4 factions, got %d" % state["factions"].size())
	if state["cities"].size() != 4:
		failures.append("expected 4 cities, got %d" % state["cities"].size())
	# A city sits on its authored city_position, not its province's area
	# centroid - _sample_provinces() offsets them by (5, 5) so this can tell
	# the difference.
	for city in state["cities"]:
		var province: int = int(city["province"])
		var expected: Vector2 = _default_positions()[province - 1] + Vector2(5, 5)
		var actual := Vector2(city["x"], city["y"])
		if actual.distance_to(expected) > 0.01:
			failures.append(
				(
					"city for province %d sits at %s, expected its city_position %s"
					% [province, actual, expected]
				)
			)

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

	# Exercise the start path with a non-default province count, since the
	# 4-province case would never catch a regression in how ownership is
	# assigned across a larger table.
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
	_start_from_provinces(manager2, positions, 10)
	var state2: Dictionary = manager2.get_state()
	if state2["factions"].size() != 4:
		failures.append("provinces start: expected 4 factions, got %d" % state2["factions"].size())
	if state2["cities"].size() != 6:
		failures.append("provinces start: expected 6 cities, got %d" % state2["cities"].size())
	if state2["provinces"].size() != 6:
		failures.append(
			"provinces start: expected 6 provinces, got %d" % state2["provinces"].size()
		)
	for city in state2["cities"]:
		if int(city.get("province", -1)) < 0:
			failures.append("city %d is not tied to a province" % int(city["id"]))

	# Conquest has to move province ownership, not just city ownership -
	# otherwise the map keeps painting the old owner's colour.
	var owners_before: Dictionary = {}
	for province in state2["provinces"]:
		owners_before[int(province["id"])] = int(province["owner"])

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

	var owners_changed := false
	for province in manager2.get_state()["provinces"]:
		if int(province["owner"]) != owners_before[int(province["id"])]:
			owners_changed = true
			break
	if not owners_changed:
		failures.append("no province changed hands over a whole campaign")

	if not manager2.is_game_over():
		failures.append("provinces start: game did not end within %d iterations" % iterations)
	if turn_started_count == 0:
		failures.append("provinces start: turn_started never fired")
	if battle_count == 0:
		failures.append("provinces start: battle_resolved never fired")
	if not game_over_fired:
		failures.append("provinces start: game_over never fired")

	print(
		(
			"campaign smoke (provinces): turns=%d battles=%d winner=%d"
			% [turn_started_count, battle_count, manager2.winner_id()]
		)
	)

	manager2.free()

	failures.append_array(_check_armies())
	_finish(failures)


## Exercises the army half of the API on a fresh game: every faction should
## start with one garrisoned army, moves are discrete province-to-province
## hops (reject a non-neighbor, land exactly on a neighbor's city, chain hops
## while the budget lasts, refuse once it's spent), and a few AI turns should
## keep armies inside the map and eventually fight.
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
	manager.load_factions(_sample_factions())
	manager.load_provinces(_army_test_provinces())
	manager.start_game_from_provinces(10)

	var state: Dictionary = manager.get_state()
	if state["armies"].size() != 4:
		failures.append("armies: expected one army per faction, got %d" % state["armies"].size())
	for army in state["armies"]:
		if int(army["garrisoned"]) == -1:
			failures.append("armies: army %d should start garrisoned" % int(army["id"]))
		if float(army["movement"]) != float(army["max_movement"]):
			failures.append(
				"armies: army %d should start with a full move budget" % int(army["id"])
			)

	# Red owns a 1-2-3-4 chain (_army_test_provinces) and its army starts in
	# province 1 - a neighbor of province 2 only, so province 3 is two hops
	# away and every hop in this test lands on Red's own soil, with no
	# coin-flip siege to derail the bookkeeping being checked here.
	var first: Dictionary = state["armies"][0]
	var max_moves: float = float(first["max_movement"])
	var province2_city := _city_position_in_province(state, 2)
	var province3_city := _city_position_in_province(state, 3)

	if manager.move_army(int(first["id"]), province3_city.x, province3_city.y):
		failures.append("armies: a move into a non-adjacent province should be rejected")

	if not manager.move_army(int(first["id"]), province2_city.x, province2_city.y):
		failures.append("armies: a move into an adjacent province was rejected")
	var after: Dictionary = _find_army(manager.get_state(), int(first["id"]))
	if after.is_empty():
		failures.append("armies: army vanished after a harmless move")
	elif Vector2(float(after["x"]), float(after["y"])).distance_to(province2_city) > 0.01:
		failures.append("armies: a hop should land exactly on the target province's city")
	elif not is_equal_approx(float(after["movement"]), max_moves - 1.0):
		failures.append(
			(
				"armies: one hop should spend exactly one move, left %.2f of %.2f"
				% [float(after["movement"]), max_moves]
			)
		)
	if army_moved_count == 0:
		failures.append("armies: army_moved never fired")

	# Chain a second hop, from province 2 into its other neighbor, province 3.
	if not manager.move_army(int(first["id"]), province3_city.x, province3_city.y):
		failures.append("armies: a second hop with moves left was rejected")
	after = _find_army(manager.get_state(), int(first["id"]))
	if not after.is_empty() and float(after["movement"]) != 0.0:
		failures.append("armies: two hops on a 2-move budget should empty it")

	# Budget spent - a third hop this turn must be refused even though
	# province 3 has further neighbors to hop to.
	var province4_city := _city_position_in_province(state, 4)
	if manager.move_army(int(first["id"]), province4_city.x, province4_city.y):
		failures.append("armies: a move with no moves left should be rejected")

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


func _city_position_in_province(state: Dictionary, province_id: int) -> Vector2:
	for city in state["cities"]:
		if int(city.get("province", -1)) == province_id:
			return Vector2(float(city["x"]), float(city["y"]))
	return Vector2.INF


func _finish(failures: Array[String]) -> void:
	if failures.is_empty():
		print("CAMPAIGN SMOKE OK")
		quit(0)
	else:
		for f in failures:
			printerr("CAMPAIGN SMOKE FAIL: ", f)
		quit(1)
