extends Node2D
## The campaign map, built from a map package (campaign/map_data/).
##
## Province geometry comes in as polygon rings the map editor already exported
## (provinces.geo.json), so this builds Area2D click targets directly from
## them. It used to scan every pixel of a flat-colored PNG in GDScript and
## re-trace each color with BitMap.opaque_to_polygons; the editor knows the
## exact shapes, so there is nothing to re-derive.
##
## A province's fill color is *not* baked into any file. It is whoever owns the
## province this turn, pushed in by apply_ownership() on every state refresh -
## which is what lets territory change hands and change color mid-game.
##
## Painted layers (terrain, resources, anything added later) mount as sprites
## in manifest order beneath the province fills, so they show through.

signal region_clicked(province_id: int)

const RegionArea := preload("res://campaign/region_area.gd")
const MapPackage := preload("res://campaign/map_package.gd")

const PACKAGE_ROOT := "res://campaign/map_data"
const BORDER_WIDTH := 0.55
const BORDER_COLOR := Color(0.05, 0.04, 0.03, 0.95)
## Painted layers sit under the province fills but over the backdrop art.
const LAYER_ALPHA := 0.55

## province id -> centroid, in the map's own pixel space (top-left origin,
## +y down). The caller decides how that lands in world space.
var province_centers: Dictionary = {}
var map_size := Vector2.ZERO
var package: MapPackage

var _areas: Dictionary = {}  ## province id -> RegionArea


func setup() -> bool:
	package = MapPackage.new()
	if not package.load_from(PACKAGE_ROOT):
		printerr("province_map: ", package.load_error)
		return false

	map_size = package.size
	province_centers = package.province_centers()

	_add_backdrop()
	_add_painted_layers()
	_add_provinces()
	return true


func _add_backdrop() -> void:
	var backdrop_path: String = PACKAGE_ROOT.path_join(
		package.manifest.get("backdrop", "backdrop.png")
	)
	if not ResourceLoader.exists(backdrop_path):
		return
	var sprite := Sprite2D.new()
	sprite.name = "Backdrop"
	sprite.texture = load(backdrop_path)
	sprite.centered = false
	# The package declares one size and every layer is exported at it, so the
	# backdrop needs no scaling. The old map stretched a 1024x820 backdrop over
	# a 1300x647 raster, which is why provinces never lined up with the coast.
	add_child(sprite)


func _add_painted_layers() -> void:
	var layers_root := Node2D.new()
	layers_root.name = "PaintedLayers"
	add_child(layers_root)

	for layer_name in package.layer_order():
		var config: Dictionary = package.layer_config(layer_name)
		# Province identity is machine-readable ids, not something to look at,
		# and the coastline is already the backdrop art.
		if config.get("kind", "") in ["identity", "mask"]:
			continue
		var raster_path: String = package.layer_raster_path(layer_name)
		if raster_path == "" or not ResourceLoader.exists(raster_path):
			continue

		var sprite := Sprite2D.new()
		sprite.name = layer_name
		sprite.texture = load(raster_path)
		sprite.centered = false
		sprite.modulate = Color(1, 1, 1, LAYER_ALPHA)
		layers_root.add_child(sprite)


func _add_provinces() -> void:
	var regions_root := Node2D.new()
	regions_root.name = "Provinces"
	add_child(regions_root)

	for province in package.provinces:
		var province_id := int(province["id"])
		var rings: Array = package.geometry.get(province_id, [])
		if rings.is_empty():
			continue

		var area := Area2D.new()
		area.set_script(RegionArea)
		area.province_id = province_id
		area.display_name = province.get("name", "Province %d" % province_id)
		area.name = "Province%d" % province_id
		area.clicked.connect(func(id: int): region_clicked.emit(id))
		regions_root.add_child(area)
		_areas[province_id] = area

		for ring in rings:
			# A hole is the boundary of an enclave another province owns. It
			# must not become a click target here, or clicks inside the enclave
			# would land on whoever surrounds it.
			if ring.get("hole", false):
				continue
			var polygon := _ring_to_polygon(ring)
			if polygon.size() < 3:
				continue

			var collision := CollisionPolygon2D.new()
			collision.polygon = polygon
			area.add_child(collision)

			var fill := Polygon2D.new()
			fill.polygon = polygon
			area.add_child(fill)

			var border := Line2D.new()
			border.points = polygon
			border.add_point(polygon[0])
			border.width = BORDER_WIDTH
			border.default_color = BORDER_COLOR
			border.joint_mode = Line2D.LINE_JOINT_ROUND
			area.add_child(border)


func _ring_to_polygon(ring: Dictionary) -> PackedVector2Array:
	var points: Array = ring.get("points", [])
	var polygon := PackedVector2Array()
	var i := 0
	while i + 1 < points.size():
		polygon.append(Vector2(points[i], points[i + 1]))
		i += 2
	return polygon


## Recolors every province from the simulation's current ownership.
##
## This is the whole mechanism by which a province changes hands visually: no
## file on disk changes, the raster is identity not politics, and the color
## comes from whoever holds it this turn.
func apply_ownership(owner_by_province: Dictionary, faction_colors: Array) -> void:
	for province_id in _areas:
		var owner: int = owner_by_province.get(province_id, -1)
		var color := Color(0.6, 0.6, 0.6)  # unowned
		if owner >= 0 and owner < faction_colors.size():
			color = faction_colors[owner]
		_areas[province_id].set_owner_color(color)
