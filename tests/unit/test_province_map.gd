extends GutTest
## Covers the pure geometry helper in campaign/province_map.gd. The rest of
## the script builds live scene nodes from a map package and is exercised by
## tools/campaign_smoke.gd instead.

const ProvinceMap := preload("res://campaign/province_map.gd")
const RegionAreaStub := preload("res://tests/fixtures/region_area_stub.gd")

var map: Node2D


func before_each() -> void:
	map = ProvinceMap.new()


func _make_stub() -> Node:
	var double := Node.new()
	double.set_script(RegionAreaStub)
	add_child_autofree(double)
	return double


func after_each() -> void:
	map.free()


func test_ring_to_polygon_pairs_flat_coords_into_points() -> void:
	var ring := {"points": [0, 0, 10, 0, 10, 10, 0, 10]}
	var polygon: PackedVector2Array = map.call("_ring_to_polygon", ring)
	assert_eq(
		polygon,
		PackedVector2Array([Vector2(0, 0), Vector2(10, 0), Vector2(10, 10), Vector2(0, 10)])
	)


func test_ring_to_polygon_drops_a_trailing_odd_coordinate() -> void:
	var ring := {"points": [0, 0, 10, 0, 10]}
	var polygon: PackedVector2Array = map.call("_ring_to_polygon", ring)
	assert_eq(polygon, PackedVector2Array([Vector2(0, 0), Vector2(10, 0)]))


func test_ring_to_polygon_empty_points_yields_empty_polygon() -> void:
	var polygon: PackedVector2Array = map.call("_ring_to_polygon", {})
	assert_eq(polygon.size(), 0)


func test_apply_ownership_colors_known_owner() -> void:
	# apply_ownership calls set_owner_color on each RegionArea; stub it out
	# instead of pulling in the full Area2D scene setup.
	var double := _make_stub()

	map._areas = {1: double}
	map.apply_ownership({1: 0}, [Color.RED, Color.BLUE])
	assert_eq(double.owner_color_calls, [Color.RED])


func test_apply_ownership_defaults_unowned_to_gray() -> void:
	var double := _make_stub()

	map._areas = {1: double}
	map.apply_ownership({}, [Color.RED, Color.BLUE])
	assert_eq(double.owner_color_calls, [Color(0.6, 0.6, 0.6)])


func test_set_highlighted_provinces_highlights_only_the_given_ids() -> void:
	var a := _make_stub()
	var b := _make_stub()
	map._areas = {1: a, 2: b}

	map.set_highlighted_provinces([2])

	assert_eq(a.highlight_calls, [false])
	assert_eq(b.highlight_calls, [true])


func test_set_highlighted_provinces_of_empty_clears_every_province() -> void:
	var a := _make_stub()
	map._areas = {1: a}
	map.set_highlighted_provinces([1])

	map.set_highlighted_provinces([])

	assert_eq(a.highlight_calls, [true, false])


func _square(x: float, y: float, side: float) -> PackedVector2Array:
	return PackedVector2Array(
		[Vector2(x, y), Vector2(x + side, y), Vector2(x + side, y + side), Vector2(x, y + side)]
	)


func test_union_all_merges_touching_polygons_into_one() -> void:
	# Two provinces of the same faction sharing a border have to come back
	# as a single shape, or the heavy frontier line gets drawn straight down
	# the middle of one realm.
	var merged: Array = map.call("_union_all", [_square(0, 0, 10), _square(9, 0, 10)])
	assert_eq(merged.size(), 1)


func test_union_all_keeps_separate_landmasses_apart() -> void:
	# A faction holding an island as well as a mainland gets an outline
	# round each, not one shape bridging the sea.
	var merged: Array = map.call("_union_all", [_square(0, 0, 10), _square(50, 50, 10)])
	assert_eq(merged.size(), 2)


func test_union_all_of_nothing_is_nothing() -> void:
	assert_eq(map.call("_union_all", []), [])
