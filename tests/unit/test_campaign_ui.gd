extends GutTest
## Covers the pure geometry helpers in campaign/campaign_ui.gd. The rest of the
## script wires up a live scene (manager, world layer, HUD) and is exercised
## visually via `make shot` / campaign_smoke.gd instead.

const CampaignUI := preload("res://campaign/campaign_ui.gd")
const MapPackage := preload("res://campaign/map_package.gd")
const ProvinceMapStub := preload("res://tests/fixtures/province_map_stub.gd")

var ui: Node2D


func before_each() -> void:
	ui = CampaignUI.new()


func after_each() -> void:
	ui.free()


func test_world_space_province_table_converts_centroid() -> void:
	ui._province_map = _stub_province_map([_province(1, [10, 20], [10, 20])])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_eq(rows[0]["centroid"], [10.0 * 20.0 - 100.0, 20.0 * 20.0 - 100.0])


## The bug this guards: city_position used to be copied verbatim from the map
## package's map-pixel space straight into the table Rust reads, while only
## centroid was converted. Rust then sited every faction's starting army on
## its raw, tiny map-pixel city_position instead of the real world-space
## point - so every army spawned stacked on top of each other near the map's
## centre instead of at their own capitals.
func test_world_space_province_table_converts_city_position_independently_of_centroid() -> void:
	ui._province_map = _stub_province_map([_province(1, [10, 20], [50, 60])])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_eq(rows[0]["city_position"], [50.0 * 20.0 - 100.0, 60.0 * 20.0 - 100.0])
	assert_ne(
		rows[0]["city_position"],
		rows[0]["centroid"],
		"city_position must convert on its own point, not fall back to centroid's"
	)


func test_world_space_province_table_leaves_city_position_out_when_package_has_none() -> void:
	var province := {"id": 1, "centroid": [10, 20]}
	ui._province_map = _stub_province_map([province])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_false(rows[0].has("city_position"))


func _province(id: int, centroid: Array, city_position: Array) -> Dictionary:
	return {"id": id, "centroid": centroid, "city_position": city_position}


func _stub_province_map(provinces: Array) -> Node:
	var package := MapPackage.new()
	package.provinces = provinces
	var stub := ProvinceMapStub.new()
	stub.package = package
	add_child_autofree(stub)
	return stub


## ---------------------------------------------------------------------------
## Zoom direction: pixels_per_unit is _base_ppu / _cam_zoom, so _cam_zoom is an
## *inverse* zoom factor - raising it shrinks pixels_per_unit. _base_ppu is a
## cover fit at _cam_zoom == 1.0, so 1.0 has to be the *ceiling* on _cam_zoom,
## not the floor: MIN_ZOOM/MAX_ZOOM used to be (1.0, 2.2), which let X (and
## scroll-down) push _cam_zoom past 1.0 and shrink pixels_per_unit below the
## cover-fit ratio - exactly the "zoom out with the X key and see grey" bug,
## distinct from (and not caught by) the focus-clamp tests above, which never
## exercised a _cam_zoom above 1.0.
## ---------------------------------------------------------------------------


func test_max_zoom_does_not_exceed_the_cover_fit_ratio() -> void:
	assert_lte(
		CampaignUI.MAX_ZOOM,
		1.0,
		"MAX_ZOOM above 1.0 shrinks pixels_per_unit below the cover-fit ratio and exposes background"
	)


func test_min_zoom_is_stricter_than_max_zoom() -> void:
	assert_lt(CampaignUI.MIN_ZOOM, CampaignUI.MAX_ZOOM)


## ---------------------------------------------------------------------------
## Camera-focus clamp: keeps the map's edge from falling short of the
## viewport's edge, which is what let the camera zoom out and reveal the
## grey background past the map.
## ---------------------------------------------------------------------------


func test_clamp_cam_focus_leaves_a_centred_focus_alone_at_cover_fit_zoom() -> void:
	# At MIN_ZOOM (1.0), pixels_per_unit is exactly the cover-fit ratio, so the
	# map exactly fills the viewport when centred - no room to pan at all.
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 0.8  # viewport.x / (2 * half_size.x) == viewport.y / (2 * half_size.y)
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(Vector2.ZERO, half_size, viewport, ppu)
	assert_eq(clamped, Vector2.ZERO)


func test_clamp_cam_focus_pulls_an_off_map_focus_back_to_the_map_edge() -> void:
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 0.8
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(
		Vector2(5000, 5000), half_size, viewport, ppu
	)
	# half_size - half_viewport_world = (1000, 500) - (500, 250)
	assert_eq(clamped, Vector2(500, 250))


func test_clamp_cam_focus_allows_panning_once_zoomed_in() -> void:
	# Doubling pixels_per_unit halves how much world the viewport covers, so
	# there is now room to pan toward an edge without exposing the background.
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 1.6
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(
		Vector2(5000, 5000), half_size, viewport, ppu
	)
	# half_size - half_viewport_world = (1000, 500) - (250, 125)
	assert_eq(clamped, Vector2(750, 375))
