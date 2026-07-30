extends GutTest
## Covers campaign/map_package.gd against a small fixture package under
## tests/fixtures/map_package/, shaped like a real export from the map
## editor but small enough to hand-check.

const MapPackage := preload("res://campaign/map_package.gd")
const FIXTURE_ROOT := "res://tests/fixtures/map_package"

var package: MapPackage


func before_each() -> void:
	package = MapPackage.new()


func test_load_from_valid_package_succeeds() -> void:
	assert_true(package.load_from(FIXTURE_ROOT))
	assert_eq(package.load_error, "")


func test_load_from_missing_package_fails() -> void:
	assert_false(package.load_from("res://tests/fixtures/does_not_exist"))
	assert_ne(package.load_error, "")


func test_size_comes_from_manifest() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.size, Vector2(100, 200))


func test_layer_order_matches_manifest() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.layer_order(), ["terrain", "provinces"])


func test_layer_config_reads_per_layer_json() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.layer_config("terrain").get("kind"), "paint")
	assert_eq(package.layer_config("nonexistent"), {})


func test_layer_raster_path_joins_layers_dir() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.layer_raster_path("terrain"), FIXTURE_ROOT.path_join("layers/terrain.png"))


func test_layer_raster_path_empty_when_layer_declares_none() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.layer_raster_path("provinces"), "")


func test_faction_colors_parsed_in_manifest_order() -> void:
	package.load_from(FIXTURE_ROOT)
	var colors := package.faction_colors()
	assert_eq(colors.size(), 2)
	assert_eq(colors[0], Color("#cd5c5c"))
	assert_eq(colors[1], Color("#6495ed"))


func test_faction_keys_parsed_in_manifest_order() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.faction_keys(), PackedStringArray(["red", "blue"]))


func test_provinces_loaded_and_indexed_by_id() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.provinces.size(), 2)
	assert_eq(package.province_by_id[1]["name"], "Province One")
	assert_eq(package.province_by_id[2]["starting_owner"], "blue")


func test_province_centers_read_centroid() -> void:
	package.load_from(FIXTURE_ROOT)
	var centers := package.province_centers()
	assert_eq(centers[1], Vector2(10, 20))
	assert_eq(centers[2], Vector2(30, 40))


func test_city_positions_reads_city_position() -> void:
	package.load_from(FIXTURE_ROOT)
	var positions := package.city_positions()
	assert_eq(positions[1], Vector2(12, 22))


func test_city_positions_falls_back_to_centroid_when_absent() -> void:
	package.load_from(FIXTURE_ROOT)
	var positions := package.city_positions()
	assert_eq(positions[2], Vector2(30, 40))


func test_geometry_loaded_by_province_id() -> void:
	package.load_from(FIXTURE_ROOT)
	assert_eq(package.geometry[1].size(), 1)
	assert_eq(package.geometry[2].size(), 2)
	assert_true(package.geometry[2][1]["hole"])


func test_load_from_strips_trailing_slash() -> void:
	assert_true(package.load_from(FIXTURE_ROOT + "/"))
	assert_eq(package.root, FIXTURE_ROOT)
