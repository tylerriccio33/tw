extends GutTest
## Scene-validation and render-invariant checks for ProvinceMap: instead of
## diffing pixels, these inspect the built scene tree after setup() - node
## counts, shader assignment, degenerate geometry. Cheap and catches most of
## what a screenshot review would (missing fill, a province that silently
## got no click target, a shader that failed to attach). The pixels
## themselves are covered separately by `make render-test`
## (tools/render_test.py) against tests/golden/.
##
## setup() loads the real campaign/map_data package directly - no
## CampaignManager needed - so this runs standalone like test_army_marker.gd.

const ProvinceMap := preload("res://campaign/province_map.gd")
const WATER_SHADER := preload("res://campaign/water.gdshader")
const COASTLINE_KEY_SHADER := preload("res://campaign/coastline_key.gdshader")

var map: Node2D


func before_each() -> void:
	map = ProvinceMap.new()
	add_child_autofree(map)


func test_setup_succeeds_against_the_real_map_package() -> void:
	assert_true(map.setup())


func test_water_layer_has_the_water_shader_assigned() -> void:
	map.setup()
	var water: ColorRect = map.get_node("Water")
	var material := water.material as ShaderMaterial
	assert_not_null(material)
	assert_eq(material.shader, WATER_SHADER)


func test_backdrop_uses_the_coastline_key_shader() -> void:
	map.setup()
	var backdrop: Sprite2D = map.get_node("Backdrop")
	var material := backdrop.material as ShaderMaterial
	assert_not_null(material)
	assert_eq(material.shader, COASTLINE_KEY_SHADER)


func test_every_province_gets_exactly_one_area_and_no_duplicate_names() -> void:
	map.setup()
	var provinces_root: Node2D = map.get_node("Provinces")
	var seen_names := {}
	for child in provinces_root.get_children():
		assert_false(seen_names.has(child.name), "duplicate province node name: %s" % child.name)
		seen_names[child.name] = true
	assert_eq(provinces_root.get_child_count(), map.package.provinces.size())


func test_every_province_area_has_a_non_degenerate_fill_polygon() -> void:
	map.setup()
	var provinces_root: Node2D = map.get_node("Provinces")
	for area in provinces_root.get_children():
		var fills := 0
		for child in area.get_children():
			if child is Polygon2D:
				fills += 1
				assert_gte(
					(child as Polygon2D).polygon.size(),
					3,
					"%s has a degenerate fill polygon" % area.name
				)
		assert_gt(fills, 0, "%s has no fill polygon at all" % area.name)


func test_apply_ownership_rebuilds_faction_borders_without_crashing() -> void:
	map.setup()
	var owner_by_province := {}
	for province_id in map.province_centers:
		owner_by_province[province_id] = 0
	map.apply_ownership(owner_by_province, [Color.RED])
	var borders_root: Node2D = map.get_node("FactionBorders")
	assert_gt(borders_root.get_child_count(), 0)


func test_apply_ownership_draws_no_borders_when_nobody_owns_anything() -> void:
	map.setup()
	map.apply_ownership({}, [])
	var borders_root: Node2D = map.get_node("FactionBorders")
	assert_eq(borders_root.get_child_count(), 0)
