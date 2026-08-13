extends GutTest
## Covers campaign/city_marker.gd's non-drawing surface: layout sizing,
## faction color updates, the anchor offset the parent map positions
## against, and the click signal. The actual glyph drawing in _draw() is
## exercised visually via the visual-change-review workflow, not here.

const CityMarker := preload("res://campaign/city_marker.gd")

var marker: Control


func before_each() -> void:
	marker = CityMarker.new()
	add_child_autofree(marker)


func test_setup_sets_city_name_and_sizes_the_control() -> void:
	marker.setup("Riverhold")
	assert_eq(marker.city_name, "Riverhold")
	assert_gt(marker.size.x, 0.0)
	assert_gt(marker.size.y, 0.0)


func test_setup_makes_the_marker_clickable() -> void:
	marker.setup("Riverhold")
	assert_eq(marker.mouse_filter, Control.MOUSE_FILTER_STOP)


func test_anchor_offset_is_the_center_of_the_keep() -> void:
	assert_eq(marker.anchor_offset(), Vector2(14, 14))


func test_set_faction_color_updates_the_color() -> void:
	marker.set_faction_color(Color.RED)
	assert_eq(marker.faction_color, Color.RED)


func test_set_faction_color_is_a_noop_for_the_same_color() -> void:
	marker.set_faction_color(Color.RED)
	# Calling again with the same color must not error or change state -
	# it exists purely to skip a redundant queue_redraw().
	marker.set_faction_color(Color.RED)
	assert_eq(marker.faction_color, Color.RED)


func test_clicked_signal_fires_on_left_mouse_press() -> void:
	watch_signals(marker)
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = true
	marker._gui_input(event)
	assert_signal_emitted(marker, "clicked")


func test_clicked_signal_does_not_fire_on_right_mouse_press() -> void:
	watch_signals(marker)
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_RIGHT
	event.pressed = true
	marker._gui_input(event)
	assert_signal_not_emitted(marker, "clicked")


func test_clicked_signal_does_not_fire_on_mouse_release() -> void:
	watch_signals(marker)
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = false
	marker._gui_input(event)
	assert_signal_not_emitted(marker, "clicked")
