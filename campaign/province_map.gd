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
##
## Borders come in two tiers, because they answer two different questions. A
## province border says "this is a different place" and is a hairline. A
## faction border says "this is a different power" and is heavy - it is the
## outline of the *union* of everything one faction holds, so provinces that
## changed hands into the same realm stop having a border between them at all.

signal region_clicked(province_id: int)

const RegionArea := preload("res://campaign/region_area.gd")
const MapPackage := preload("res://campaign/map_package.gd")

const PACKAGE_ROOT := "res://campaign/map_data"
## Painted layers sit under the province fills but over the backdrop art.
const LAYER_ALPHA := 0.55

# Widths are in map pixels; the whole node is scaled up by the caller, so
# these stay proportional to the map rather than to the window.
const PROVINCE_BORDER_WIDTH := 1.1
const PROVINCE_BORDER_COLOR := Color(1.0, 1.0, 1.0, 0.35)
const FACTION_BORDER_WIDTH := 4.0
const FACTION_BORDER_COLOR := Color(0.04, 0.03, 0.02, 0.92)

## Provinces are traced from a raster one id per pixel, so two that share a
## border come out roughly a pixel apart rather than sharing vertices. Union
## them as-is and that seam survives as a hairline crack, which would then
## get drawn as a faction border straight down the middle of one realm.
## Growing each province by more than the seam is wide closes it.
const FACTION_BORDER_GROW := 2.5

const WATER_SHADER := preload("res://campaign/water.gdshader")
const COASTLINE_KEY_SHADER := preload("res://campaign/coastline_key.gdshader")

## province id -> centroid, in the map's own pixel space (top-left origin,
## +y down). The caller decides how that lands in world space.
var province_centers: Dictionary = {}
## province id -> authored city position, falling back to centroid. Same
## pixel space as province_centers.
var city_positions: Dictionary = {}
var map_size := Vector2.ZERO
var package: MapPackage

var _areas: Dictionary = {}  ## province id -> RegionArea
## province id -> Array of outer-ring PackedVector2Array, for faction unions.
var _outer_rings: Dictionary = {}
var _faction_borders_root: Node2D
## The ownership the faction borders were last built for. Rebuilding a set of
## polygon unions on every state refresh would be work for nothing; ownership
## only changes when a province is taken.
var _border_ownership: Dictionary = {}


func setup() -> bool:
	package = MapPackage.new()
	if not package.load_from(PACKAGE_ROOT):
		printerr("province_map: ", package.load_error)
		return false

	map_size = package.size
	province_centers = package.province_centers()
	city_positions = package.city_positions()

	_add_water()
	_add_backdrop()
	_add_painted_layers()
	_add_provinces()
	_add_faction_borders()
	return true


## The animated water sits behind everything; the backdrop's own sea fill is
## chroma-keyed to transparent (see _add_backdrop) so it shows through.
func _add_water() -> void:
	var sea_color := _sea_color()
	var rect := ColorRect.new()
	rect.name = "Water"
	rect.color = sea_color
	rect.size = map_size
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var material := ShaderMaterial.new()
	material.shader = WATER_SHADER
	material.set_shader_parameter("deep_color", sea_color)
	rect.material = material
	add_child(rect)


## The mask layer's legend is the map package's own record of which color
## means "sea" - read it rather than hardcoding a color per-map.
func _sea_color() -> Color:
	return _mask_color("sea", Color(0.114, 0.208, 0.314))


func _mask_color(key: String, fallback: Color) -> Color:
	for layer_name in package.layer_order():
		var config: Dictionary = package.layer_config(layer_name)
		if config.get("kind", "") != "mask":
			continue
		for hex_key in config.get("legend", {}):
			if config["legend"][hex_key].get("key", "") == key:
				return Color(hex_key)
	return fallback


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
	# Both its flat fills are cut to transparent, leaving only the drawn
	# coastline stroke: the sea fill so the animated Water layer added just
	# before it shows through, the land fill because the province fills cover
	# the land (see coastline_key.gdshader).
	var material := ShaderMaterial.new()
	material.shader = COASTLINE_KEY_SHADER
	material.set_shader_parameter("key_color", _sea_color())
	sprite.material = material
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

		var outer: Array = []
		for ring in rings:
			var polygon := _ring_to_polygon(ring)
			if polygon.size() < 3:
				continue

			# A hole is the boundary of an enclave another province owns. It
			# must not become a click target or a fill here, or clicks inside
			# the enclave would land on whoever surrounds it - but it is still
			# a province border, so it gets the hairline like any other edge.
			if not ring.get("hole", false):
				outer.append(polygon)

				var collision := CollisionPolygon2D.new()
				collision.polygon = polygon
				area.add_child(collision)

				var fill := Polygon2D.new()
				fill.polygon = polygon
				area.add_child(fill)

			area.add_child(_outline(polygon, PROVINCE_BORDER_WIDTH, PROVINCE_BORDER_COLOR))

		_outer_rings[province_id] = outer


## A closed Line2D tracing one ring.
func _outline(polygon: PackedVector2Array, width: float, color: Color) -> Line2D:
	var line := Line2D.new()
	line.points = polygon
	line.add_point(polygon[0])
	line.width = width
	line.default_color = color
	line.joint_mode = Line2D.LINE_JOINT_ROUND
	return line


## Heavy outlines around each realm, drawn over the province fills and
## rebuilt from simulation state - see _rebuild_faction_borders.
func _add_faction_borders() -> void:
	_faction_borders_root = Node2D.new()
	_faction_borders_root.name = "FactionBorders"
	add_child(_faction_borders_root)


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

	if owner_by_province != _border_ownership:
		_border_ownership = owner_by_province.duplicate()
		_rebuild_faction_borders(owner_by_province)


## Tints exactly the given provinces as legal move targets and clears the
## highlight everywhere else - see region_area.set_highlight. `ids` holds
## whatever the manager's reachable_provinces() returned, so an empty array
## (no army selected, or it has no moves left) clears every highlight.
func set_highlighted_provinces(ids: Array) -> void:
	for province_id in _areas:
		_areas[province_id].set_highlight(province_id in ids)


## Outlines each realm as one shape rather than outlining its provinces
## separately, so an internal border between two provinces of the same faction
## carries only the province hairline and the heavy line follows the frontier.
##
## Recomputed from ownership alone, which means conquest redraws the frontier
## with nothing on disk changing - the same property province colors have.
func _rebuild_faction_borders(owner_by_province: Dictionary) -> void:
	if _faction_borders_root == null:
		return  # no setup() yet, so there is nothing on screen to redraw
	for child in _faction_borders_root.get_children():
		child.queue_free()

	var rings_by_owner: Dictionary = {}
	for province_id in _outer_rings:
		var owner: int = owner_by_province.get(province_id, -1)
		if owner < 0:
			continue  # nobody holds it, so there is no frontier to draw
		var grown: Array = rings_by_owner.get(owner, [])
		for ring in _outer_rings[province_id]:
			# Grow before merging, not after, so the seam between neighbours
			# is gone by the time the union runs. See FACTION_BORDER_GROW.
			for piece in Geometry2D.offset_polygon(ring, FACTION_BORDER_GROW):
				grown.append(piece)
		rings_by_owner[owner] = grown

	for owner in rings_by_owner:
		for polygon in _union_all(rings_by_owner[owner]):
			if polygon.size() < 3:
				continue
			_faction_borders_root.add_child(
				_outline(polygon, FACTION_BORDER_WIDTH, FACTION_BORDER_COLOR)
			)


## Merges every polygon that touches into one, leaving genuinely separate
## landmasses separate - a faction holding an island and a mainland gets an
## outline round each, not one shape bridging the sea.
func _union_all(polygons: Array) -> Array:
	var merged: Array = []
	for polygon in polygons:
		var pending: PackedVector2Array = polygon
		var disjoint: Array = []
		for existing in merged:
			var union: Array = Geometry2D.merge_polygons(existing, pending)
			# One polygon back means they overlapped and are now a single
			# shape; anything else means they never touched.
			if union.size() == 1:
				pending = union[0]
			else:
				disjoint.append(existing)
		disjoint.append(pending)
		merged = disjoint
	return merged
