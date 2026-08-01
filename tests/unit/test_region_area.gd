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


func test_set_highlight_overrides_the_owner_tint_with_the_highlight_color() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)

	region.set_highlight(true)

	assert_eq(poly.color, Color(RegionArea.HIGHLIGHT_COLOR, RegionArea.HIGHLIGHT_ALPHA))


func test_set_highlight_false_restores_the_owner_tint() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)
	region.set_highlight(true)

	region.set_highlight(false)

	assert_eq(poly.color, Color(Color.RED, 0.55))


func test_hovering_a_highlighted_province_uses_the_highlight_hover_alpha() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)
	region.set_highlight(true)

	region._on_mouse_entered()

	assert_eq(poly.color, Color(RegionArea.HIGHLIGHT_COLOR, RegionArea.HIGHLIGHT_HOVER_ALPHA))


func test_set_highlight_to_its_current_value_is_a_noop() -> void:
	var poly := _add_polygon()
	region.set_owner_color(Color.RED)
	# Distinguishable from a real repaint: if set_highlight(false) skipped its
	# early-return guard it would still call _repaint and clobber this.
	poly.color = Color.MAGENTA

	region.set_highlight(false)

	assert_eq(poly.color, Color.MAGENTA)


func test_add_highlight_border_starts_hidden() -> void:
	var polygon := PackedVector2Array([Vector2(0, 0), Vector2(10, 0), Vector2(10, 10)])
	region.add_highlight_border(polygon)

	var line := region.get_child(0)
	assert_true(line is Line2D)
	assert_false(line.visible)


func test_set_highlight_true_shows_the_thick_green_border() -> void:
	var polygon := PackedVector2Array([Vector2(0, 0), Vector2(10, 0), Vector2(10, 10)])
	region.add_highlight_border(polygon)

	region.set_highlight(true)

	var line: Line2D = region.get_child(0)
	assert_true(line.visible)
	assert_eq(line.default_color, RegionArea.HIGHLIGHT_BORDER_COLOR)
	assert_eq(line.width, RegionArea.HIGHLIGHT_BORDER_WIDTH)
	assert_true(
		line.width > 1.1, "must be noticeably thicker than the 1.1px province hairline"
	)


func test_set_highlight_false_hides_the_thick_green_border_again() -> void:
	var polygon := PackedVector2Array([Vector2(0, 0), Vector2(10, 0), Vector2(10, 10)])
	region.add_highlight_border(polygon)
	region.set_highlight(true)

	region.set_highlight(false)

	var line: Line2D = region.get_child(0)
	assert_false(line.visible)


func test_highlight_border_traces_the_given_polygon_as_a_closed_loop() -> void:
	var polygon := PackedVector2Array([Vector2(0, 0), Vector2(10, 0), Vector2(10, 10)])
	region.add_highlight_border(polygon)

	var line: Line2D = region.get_child(0)
	assert_eq(line.points.size(), 4, "closed loop repeats the first point")
	assert_eq(line.points[0], line.points[3])
