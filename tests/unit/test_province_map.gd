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
