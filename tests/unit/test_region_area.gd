extends GutTest
## Covers campaign/region_area.gd: the fill color/alpha it pushes to its
## Polygon2D children, hover state, and the click signal it re-emits with
## its own province_id.

const RegionArea := preload("res://campaign/region_area.gd")

var region: Area2D


func before_each() -> void:
	region = RegionArea.new()
	add_child_autofree(region)


func _add_polygon() -> Polygon2D:
	var poly := Polygon2D.new()
	region.add_child(poly)
	return poly


func test_set_owner_color_tints_existing_children_at_fill_alpha() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)
	assert_eq(poly.color, Color(Color.RED, 0.55))


func test_hover_raises_alpha_and_exit_restores_it() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)

	region._on_mouse_entered()
	assert_eq(poly.color, Color(Color.RED, 0.85))

	region._on_mouse_exited()
	assert_eq(poly.color, Color(Color.RED, 0.55))


func test_a_polygon_added_after_hover_starts_picks_up_the_hover_alpha() -> void:
	region.set_owner_color(Color.RED)
	region._on_mouse_entered()

	var poly := _add_polygon()
	region._on_child_entered_tree(poly)

	assert_eq(poly.color, Color(Color.RED, 0.85))


func test_a_non_polygon_child_is_ignored() -> void:
	var node := Node.new()
	region.add_child(node)
	region._on_child_entered_tree(node)
	assert_eq(node.get_parent(), region, "must not error or reparent the node")


func test_clicked_signal_carries_the_province_id_on_left_click() -> void:
	region.province_id = 7
	watch_signals(region)

	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = true
	region._on_input_event(null, event, 0)

	assert_signal_emitted_with_parameters(region, "clicked", [7])


func test_clicked_signal_does_not_fire_on_right_click() -> void:
	region.province_id = 7
	watch_signals(region)

	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_RIGHT
	event.pressed = true
	region._on_input_event(null, event, 0)

	assert_signal_not_emitted(region, "clicked")
