extends GutTest
## Covers campaign/army_layer.gd's non-animated surface: marker sync from a
## state snapshot, selection rules, and order gating. The tween-driven
## marching animation is exercised visually instead - it needs a live frame
## loop to observe.

const ArmyLayer := preload("res://campaign/army_layer.gd")
const ManagerStub := preload("res://tests/fixtures/army_manager_stub.gd")

var layer: Control
var manager: Node


func _identity(v: Vector2) -> Vector2:
	return v


func before_each() -> void:
	layer = ArmyLayer.new()
	add_child_autofree(layer)
	manager = ManagerStub.new()
	add_child_autofree(manager)
	var colors: Array[Color] = [Color.RED, Color.BLUE]
	layer.setup(manager, _identity, _identity, colors)


func _army(id: int, owner: int, x: float, y: float) -> Dictionary:
	return {
		"id": id,
		"owner": owner,
		"name": "Army %d" % id,
		"x": x,
		"y": y,
		"movement": 3.0,
		"max_movement": 5.0,
		"garrisoned": -1,
	}


func test_sync_creates_one_marker_per_living_army() -> void:
	manager.armies = [_army(1, 0, 10, 10), _army(2, 1, 20, 20)]
	layer.sync(manager.get_state())
	assert_eq(layer._markers.size(), 2)


func test_sync_removes_a_marker_whose_army_died() -> void:
	manager.armies = [_army(1, 0, 10, 10), _army(2, 1, 20, 20)]
	layer.sync(manager.get_state())

	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())

	assert_eq(layer._markers.size(), 1)
	assert_true(layer._markers.has(1))


func test_sync_deselects_when_the_selected_army_dies() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	assert_eq(layer.selected_army_id(), 1)

	manager.armies = []
	layer.sync(manager.get_state())

	assert_eq(layer.selected_army_id(), -1)


func test_project_positions_each_marker_at_its_ground_position() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	var marker: Panel = layer._markers[1]
	assert_eq(marker.position, Vector2(10, 10) - marker.size / 2.0)


func test_select_accepts_a_player_owned_army() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	assert_eq(layer.selected_army_id(), 1)


func test_select_refuses_an_enemy_owned_army() -> void:
	manager.armies = [_army(1, 1, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	assert_eq(layer.selected_army_id(), -1)


func test_select_minus_one_clears_the_selection() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	layer.select(-1)
	assert_eq(layer.selected_army_id(), -1)


func test_order_selected_to_does_nothing_with_no_selection() -> void:
	layer.order_selected_to(Vector2(5, 5))
	assert_eq(manager.move_army_calls.size(), 0)


func test_order_selected_to_is_blocked_while_orders_are_locked() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	layer.set_orders_locked(true)

	layer.order_selected_to(Vector2(5, 5))

	assert_eq(manager.move_army_calls.size(), 0)


func test_order_selected_to_is_blocked_when_it_is_not_the_players_turn() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	manager.faction_id = 1

	layer.order_selected_to(Vector2(5, 5))

	assert_eq(manager.move_army_calls.size(), 0)


func test_order_selected_to_issues_a_move_and_emits_state_changed() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	watch_signals(layer)

	layer.order_selected_to(Vector2(5, 5))

	assert_eq(manager.move_army_calls, [[1, 5.0, 5.0]])
	assert_signal_emitted(layer, "state_changed")


func test_order_selected_to_logs_a_garrison_message_when_the_army_enters_a_city() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	manager.garrison_result = 42
	watch_signals(layer)

	layer.order_selected_to(Vector2(5, 5))

	assert_signal_emitted_with_parameters(layer, "log_message", ["Army garrisons in city 42."])


func test_order_selected_at_screen_ignores_a_click_off_the_world() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	layer._screen_to_world = func(_p): return Vector2.INF

	layer.order_selected_at_screen(Vector2(5, 5))

	assert_eq(manager.move_army_calls.size(), 0)
