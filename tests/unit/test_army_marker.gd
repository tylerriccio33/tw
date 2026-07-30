extends GutTest
## Covers campaign/army_marker.gd's non-drawing surface: layout sizing,
## faction/selection state, and the anchor offset the parent layer positions
## against. The spear-and-pennant glyph drawn in _draw() is exercised
## visually via the visual-change-review workflow, not here.

const ArmyMarker := preload("res://campaign/army_marker.gd")

var marker: Control


func before_each() -> void:
	marker = ArmyMarker.new()
	add_child_autofree(marker)


func test_setup_sizes_the_control() -> void:
	marker.setup()
	assert_gt(marker.size.x, 0.0)
	assert_gt(marker.size.y, 0.0)


func test_setup_makes_the_marker_clickable() -> void:
	marker.setup()
	assert_eq(marker.mouse_filter, Control.MOUSE_FILTER_STOP)


func test_anchor_offset_is_the_foot_of_the_spear() -> void:
	marker.setup()
	assert_eq(marker.anchor_offset(), Vector2(marker.SIZE.x / 2.0, marker.SIZE.y))


func test_set_faction_color_updates_the_color() -> void:
	marker.set_faction_color(Color.RED)
	assert_eq(marker.faction_color, Color.RED)


func test_set_faction_color_is_a_noop_for_the_same_color() -> void:
	marker.set_faction_color(Color.RED)
	# Calling again with the same color must not error or change state -
	# it exists purely to skip a redundant queue_redraw().
	marker.set_faction_color(Color.RED)
	assert_eq(marker.faction_color, Color.RED)


func test_set_selected_updates_state() -> void:
	marker.set_selected(true)
	assert_true(marker.selected)


func test_set_selected_is_a_noop_for_the_same_state() -> void:
	marker.set_selected(true)
	marker.set_selected(true)
	assert_true(marker.selected)
