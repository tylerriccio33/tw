extends GutTest
## Covers campaign/army_layer.gd's non-animated surface: marker sync from a
## state snapshot, selection rules, and order gating. The tween-driven
## marching animation is exercised visually instead - it needs a live frame
## loop to observe.

const ArmyLayer := preload("res://campaign/army_layer.gd")
const ManagerStub := preload("res://tests/fixtures/army_manager_stub.gd")
const ProvinceMapStub := preload("res://tests/fixtures/province_map_stub.gd")

var layer: Control
var manager: Node
var province_map: Node2D


func _identity(v: Vector2) -> Vector2:
	return v


func before_each() -> void:
	layer = ArmyLayer.new()
	add_child_autofree(layer)
	manager = ManagerStub.new()
	add_child_autofree(manager)
	province_map = ProvinceMapStub.new()
	add_child_autofree(province_map)
	var colors: Array[Color] = [Color.RED, Color.BLUE]
	layer.setup(manager, _identity, _identity, colors, province_map)


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


func test_project_positions_each_marker_beside_its_ground_position() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	var marker: Control = layer._markers[1]
	assert_eq(marker.position, Vector2(10, 10) + ArmyLayer.DRAW_OFFSET - marker.anchor_offset())


## Render invariant: two armies with distinct model positions must land on
## distinct screen positions. This is the layer that would have caught the
## "every faction's starting army landed stacked at the map centre" bug
## (0434ac4) if the root cause had been here instead of upstream in
## campaign_ui.gd's world-space conversion (see test_campaign_ui.gd) -
## kept as a standing guard against any future bug in this projection.
func test_project_gives_armies_at_different_ground_positions_different_screen_positions() -> void:
	manager.armies = [_army(1, 0, 10, 10), _army(2, 1, 200, 300)]
	layer.sync(manager.get_state())
	var a: Control = layer._markers[1]
	var b: Control = layer._markers[2]
	assert_ne(a.position, b.position)


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


## ---------------------------------------------------------------------------
## Marker clicks (_on_marker_input) - select-then-click-destination
## ---------------------------------------------------------------------------


func _left_click() -> InputEventMouseButton:
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = true
	return event


func _right_click() -> InputEventMouseButton:
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_RIGHT
	event.pressed = true
	return event


func test_clicking_a_friendly_army_with_nothing_selected_selects_it() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())

	layer._on_marker_input(_left_click(), 1)

	assert_eq(layer.selected_army_id(), 1)
	assert_eq(manager.move_army_calls.size(), 0)


func test_clicking_an_enemy_army_with_a_friendly_army_selected_issues_a_move_order() -> void:
	manager.armies = [_army(1, 0, 10, 10), _army(2, 1, 40, 40)]
	layer.sync(manager.get_state())
	layer.select(1)

	layer._on_marker_input(_left_click(), 2)

	assert_eq(manager.move_army_calls, [[1, 40.0, 40.0]])
	# The order is aimed at army 1 (still selected), not a reselection onto 2.
	assert_eq(layer.selected_army_id(), 1)


func test_clicking_a_different_friendly_army_reselects_instead_of_marching_onto_it() -> void:
	# Regression: army A selected, then clicking army B (also ours) used to
	# be read as a move order marching A onto B instead of switching command
	# to B - clicking your own army should always select it.
	manager.armies = [_army(1, 0, 10, 10), _army(2, 0, 40, 40)]
	layer.sync(manager.get_state())
	layer.select(1)

	layer._on_marker_input(_left_click(), 2)

	assert_eq(layer.selected_army_id(), 2)
	assert_eq(manager.move_army_calls.size(), 0)


func test_right_clicking_any_army_while_selected_issues_an_attack_order() -> void:
	manager.armies = [_army(1, 0, 10, 10), _army(2, 1, 40, 40)]
	layer.sync(manager.get_state())
	layer.select(1)

	layer._on_marker_input(_right_click(), 2)

	assert_eq(manager.move_army_calls, [[1, 40.0, 40.0]])


func test_select_then_click_destination_dispatches_a_move_order_to_the_engine() -> void:
	# The full select -> click-destination interaction: select a friendly
	# army via its marker, then click an off-map destination (as
	# campaign_ui.gd's ground-miss handler would), and confirm the order
	# actually reaches the engine/sim layer (manager.move_army), not just a
	# visual-only change.
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())

	layer._on_marker_input(_left_click(), 1)
	assert_eq(layer.selected_army_id(), 1)

	layer.order_selected_at_screen(Vector2(200, 200))

	assert_eq(manager.move_army_calls, [[1, 200.0, 200.0]])


## ---------------------------------------------------------------------------
## Province highlighting (_update_highlighted_provinces)
## ---------------------------------------------------------------------------


func test_selecting_an_army_highlights_its_reachable_provinces() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	manager.reachable_by_army[1] = [7, 5]
	layer.sync(manager.get_state())

	layer.select(1)

	assert_eq(province_map.highlighted_calls.back(), [7, 5])


func test_deselecting_clears_the_highlight() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	manager.reachable_by_army[1] = [7, 5]
	layer.sync(manager.get_state())
	layer.select(1)

	layer.select(-1)

	assert_eq(province_map.highlighted_calls.back(), [])


func test_sync_refreshes_the_highlight_for_the_current_selection() -> void:
	# moves_left can drop without a reselect (an order was just issued), so a
	# plain sync() has to re-pull reachable_provinces, not just the initial
	# select() call.
	manager.armies = [_army(1, 0, 10, 10)]
	manager.reachable_by_army[1] = [7, 5]
	layer.sync(manager.get_state())
	layer.select(1)

	manager.reachable_by_army[1] = []
	layer.sync(manager.get_state())

	assert_eq(province_map.highlighted_calls.back(), [])


func test_exit_tree_clears_the_highlight() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	manager.reachable_by_army[1] = [7]
	layer.sync(manager.get_state())
	layer.select(1)

	layer._exit_tree()

	assert_eq(province_map.highlighted_calls.back(), [])


## ---------------------------------------------------------------------------
## Cursor state (_cursor_state_for)
## ---------------------------------------------------------------------------


func _city(province: int, x: float, y: float) -> Dictionary:
	return {"id": province, "province": province, "x": x, "y": y}


func test_cursor_is_none_with_no_selection() -> void:
	assert_eq(layer._cursor_state_for(Vector2.ZERO), ArmyLayer.CursorState.NONE)


func test_cursor_is_none_while_orders_are_locked() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	layer.sync(manager.get_state())
	layer.select(1)
	layer.set_orders_locked(true)

	assert_eq(layer._cursor_state_for(Vector2.ZERO), ArmyLayer.CursorState.NONE)


func test_cursor_is_order_when_selected_and_not_hovering_a_reachable_city() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	manager.cities = [_city(7, 500, 500)]
	manager.reachable_by_army[1] = [7]
	layer.sync(manager.get_state())
	layer.select(1)

	assert_eq(layer._cursor_state_for(Vector2.ZERO), ArmyLayer.CursorState.ORDER)


func test_cursor_is_order_reachable_when_hovering_a_reachable_citys_screen_position() -> void:
	manager.armies = [_army(1, 0, 10, 10)]
	manager.cities = [_city(7, 500, 500)]
	manager.reachable_by_army[1] = [7]
	layer.sync(manager.get_state())
	layer.select(1)

	assert_eq(layer._cursor_state_for(Vector2(500, 500)), ArmyLayer.CursorState.ORDER_REACHABLE)


func test_cursor_ignores_a_city_whose_province_is_not_reachable() -> void:
	# A city sitting right under the mouse still must not read as reachable
	# if its province isn't in reachable_provinces - e.g. an enemy city two
	# hops away that just happens to render nearby on screen.
	manager.armies = [_army(1, 0, 10, 10)]
	manager.cities = [_city(9, 500, 500)]
	manager.reachable_by_army[1] = [7]
	layer.sync(manager.get_state())
	layer.select(1)

	assert_eq(layer._cursor_state_for(Vector2(500, 500)), ArmyLayer.CursorState.ORDER)
