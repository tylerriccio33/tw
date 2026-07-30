extends RefCounted
## Reads a map package from disk.
##
## A package is a manifest (map.json) plus one raster and one JSON legend per
## layer, and two derived files the editor's export produces:
##
##   provinces.table.json  what each province *is* - neighbors, starting owner,
##                         and whatever tags its painted layers reduced to.
##                         Handed straight to the Rust side.
##   provinces.geo.json    what each province *looks like* - polygon rings in
##                         map pixel space.
##
## Shipping the rings matters. The old map was a flat-colored PNG that
## province_map.gd scanned pixel by pixel in GDScript (840k Image.get_pixel
## calls at startup) and re-traced with BitMap.opaque_to_polygons. The editor
## already knows the exact geometry, so it exports it and nothing here has to
## re-derive it.
##
## Nothing in this file names a layer. Adding one to map.json makes it load,
## render, and reach the simulation with no change here.

const MANIFEST_PATH := "map.json"
const TABLE_PATH := "provinces.table.json"
const GEO_PATH := "provinces.geo.json"
const LAYERS_DIR := "layers"

var root: String = ""
var manifest: Dictionary = {}
var factions: Array = []
var provinces: Array = []
var province_by_id: Dictionary = {}
var geometry: Dictionary = {}  ## province id -> Array of {points, hole}
var layer_configs: Dictionary = {}
var size: Vector2 = Vector2.ZERO
var load_error: String = ""


## Returns false and sets load_error if the package can't be read. Callers
## should surface that rather than running on a half-loaded map.
func load_from(package_root: String) -> bool:
	root = package_root.rstrip("/")

	manifest = _read_json(root.path_join(MANIFEST_PATH), {})
	if manifest.is_empty():
		load_error = "no readable %s in %s" % [MANIFEST_PATH, root]
		return false

	var declared_size: Array = manifest.get("size", [])
	if declared_size.size() != 2:
		load_error = "%s has no size" % MANIFEST_PATH
		return false
	size = Vector2(declared_size[0], declared_size[1])

	factions = _read_json(root.path_join(manifest.get("factions", "factions.json")), [])

	for layer_name in manifest.get("layers", []):
		var config_path := root.path_join(LAYERS_DIR).path_join("%s.json" % layer_name)
		layer_configs[layer_name] = _read_json(config_path, {})

	provinces = _read_json(root.path_join(TABLE_PATH), {}).get("provinces", [])
	if provinces.is_empty():
		load_error = "%s has no provinces - export from the map editor first" % TABLE_PATH
		return false
	for province in provinces:
		province_by_id[int(province["id"])] = province

	for entry in _read_json(root.path_join(GEO_PATH), {}).get("provinces", []):
		geometry[int(entry["id"])] = entry.get("rings", [])
	if geometry.is_empty():
		load_error = "%s has no geometry - export from the map editor first" % GEO_PATH
		return false

	return true


func layer_order() -> Array:
	return manifest.get("layers", [])


func layer_config(layer_name: String) -> Dictionary:
	return layer_configs.get(layer_name, {})


## Absolute res:// path to a layer's raster, or "" if the layer declares none.
func layer_raster_path(layer_name: String) -> String:
	var config: Dictionary = layer_config(layer_name)
	var raster: String = config.get("raster", "")
	if raster == "":
		return ""
	return root.path_join(LAYERS_DIR).path_join(raster)


## Faction key -> Color, from factions.json. The simulation reports a province's
## owner by id, and campaign_ui maps that through this.
func faction_colors() -> Array[Color]:
	var colors: Array[Color] = []
	for faction in factions:
		colors.append(Color(faction.get("color", "#ffffff")))
	return colors


func faction_keys() -> PackedStringArray:
	var keys := PackedStringArray()
	for faction in factions:
		keys.append(faction.get("key", ""))
	return keys


## Province id -> its area centroid, in map pixel space. The caller decides how
## that lands in world space.
func province_centers() -> Dictionary:
	var centers := {}
	for province in provinces:
		var centroid: Array = province.get("centroid", [0, 0])
		centers[int(province["id"])] = Vector2(centroid[0], centroid[1])
	return centers


## Province id -> its authored city position, in map pixel space. Falls back
## to the province's centroid for packages exported before cities existed.
func city_positions() -> Dictionary:
	var positions := {}
	for province in provinces:
		var point: Array = province.get("city_position", province.get("centroid", [0, 0]))
		positions[int(province["id"])] = Vector2(point[0], point[1])
	return positions


func _read_json(path: String, fallback: Variant) -> Variant:
	if not FileAccess.file_exists(path):
		return fallback
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		printerr("map_package: could not open ", path)
		return fallback
	var parsed: Variant = JSON.parse_string(file.get_as_text())
	if parsed == null:
		printerr("map_package: ", path, " is not valid JSON")
		return fallback
	return parsed
