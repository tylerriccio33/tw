extends Node3D

## Settlements: clusters of hand-modelled buildings with an optional wall ring.
##
## Houses and keeps are assembled at build time from Quaternius's CC0
## Medieval Village MegaKit (assets/buildings/kit/, vendored by `make
## buildings` - see tools/fetch_buildings.py) rather than generated from
## primitives: a handful of textured pieces (floor/walls/roof) glued into one
## merged mesh per building variant, then scattered the same way the old
## procedural boxes were - one MultiMesh draw call per variant, so this costs
## nothing extra at render time over the shape it replaced. Curtain wall and
## towers stay procedural: the kit has no fortification pieces, and at
## campaign-map zoom a plain stone ring reads fine on its own.

const KIT_DIR := "res://assets/buildings/kit/"

## A wall piece's local frame: front face at local +Z (thin lip out to
## z=0.092), thickness runs back to z=-0.314, spans the full 2m tile width in
## X, base at y=0, top at y=3.123 - the eave height every roof aligns to. Read
## once from the glTF accessors' min/max, not eyeballed.
const WALL_TOP := 3.123
const TILE_HALF := 1.0

## Per-roof-piece vertical offset from its own pivot down to its lowest point
## (eave). Adding this to WALL_TOP (or a stacked storey's top) puts the eave
## flush with the wall below it.
const ROOF_EAVE_DROP := {
	"Roof_RoundTiles_4x4": 0.516,
	"Roof_RoundTiles_4x6": 0.516,
	"Roof_RoundTiles_6x4": 0.782,
	"Roof_Tower_RoundTiles": 0.572,
}

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

## City centres in world space, published for the road network in splines.gd.
var city_centres: PackedVector3Array = []

## Radius of each city, index-aligned with `city_centres`. Roads stop at the
## wall rather than driving through the middle of the buildings.
var city_radii: PackedFloat32Array = []

## Piece name -> loaded Mesh, so a piece shared by several recipes (e.g. every
## variant's floor slab) is only loaded off disk once per render.
var _piece_cache: Dictionary = {}


func build(cfg: Dictionary, seed_value: int, map_extent: float, terrain_builder: Node) -> void:
	var count := int(cfg["count"])
	if count < 1:
		errors.append("cities.count must be >= 1, got %d" % count)
		return

	var min_distance := float(cfg["min_distance"])
	var min_height := float(cfg["min_height"])
	var max_height := float(cfg["max_height"])
	var max_slope := float(cfg["max_slope_degrees"])
	var attempts := int(cfg["placement_attempts"])

	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value + 707

	# Dart throwing with a minimum-separation test: simpler than a true
	# Poisson-disc sampler and entirely adequate for a handful of cities.
	var placed := 0
	for _attempt in attempts:
		if placed >= count:
			break
		var x := rng.randf_range(-map_extent, map_extent)
		var z := rng.randf_range(-map_extent, map_extent)

		var h: float = terrain_builder.height_at(x, z)
		if not is_finite(h) or h < min_height or h > max_height:
			continue
		var slope: float = terrain_builder.slope_at(x, z)
		if not is_finite(slope) or slope > max_slope:
			continue

		var too_close := false
		for existing in city_centres:
			if Vector2(existing.x - x, existing.z - z).length() < min_distance:
				too_close = true
				break
		if too_close:
			continue

		city_centres.append(Vector3(x, h, z))
		city_radii.append(float(cfg["radius"]))
		placed += 1

	if placed == 0:
		errors.append(
			(
				(
					"placed 0 cities in %d attempts - the gate (height %.0f..%.0f, "
					% [attempts, min_height, max_height]
				)
				+ "slope <= %.0f deg) may exclude the whole landmass" % max_slope
			)
		)
		return
	if placed < count:
		# Not fatal: a smaller map genuinely may not fit the requested count at
		# the requested separation. Worth saying out loud rather than hiding.
		print(
			"cities: placed %d of %d requested (min_distance %.0f)" % [placed, count, min_distance]
		)

	var recipes := _house_recipes()
	var house_transforms: Array[Array] = []
	for _i in recipes.size():
		house_transforms.append([] as Array[Transform3D])
	var wall_transforms: Array[Transform3D] = []
	var tower_transforms: Array[Transform3D] = []
	var keep_transforms: Array[Transform3D] = []

	for i in city_centres.size():
		_lay_out_city(
			cfg,
			rng,
			terrain_builder,
			city_centres[i],
			city_radii[i],
			recipes.size(),
			house_transforms,
			wall_transforms,
			tower_transforms,
			keep_transforms
		)

	for i in recipes.size():
		_add_multimesh(
			"Houses%d" % i, _merge_pieces(recipes[i]), house_transforms[i] as Array[Transform3D]
		)
	_add_multimesh("Keeps", _merge_pieces(_keep_recipe()), keep_transforms)

	var building_size := float(cfg["building_size"])
	_add_multimesh("Curtain", _make_curtain(cfg, building_size), wall_transforms)
	_add_multimesh("Towers", _make_tower(cfg, building_size), tower_transforms)

	built = true


## City count, for the harness's per-render stats block.
func stats() -> Dictionary:
	return {"cities": city_centres.size()}


func _lay_out_city(
	cfg: Dictionary,
	rng: RandomNumberGenerator,
	terrain_builder: Node,
	centre: Vector3,
	radius: float,
	variant_count: int,
	houses: Array[Array],
	walls: Array[Transform3D],
	towers: Array[Transform3D],
	keeps: Array[Transform3D],
) -> void:
	var building_count := int(cfg["building_count"])
	var max_building_slope := float(cfg["max_building_slope_degrees"])
	# building_size was tuned against the old procedural roof, a cone of
	# diameter size*1.9. The kit roof's local footprint (eaves included) is
	# ~5.5m regardless of which tile it sits on, so matching that same
	# diameter is what keeps city density (buildings per unit area) looking
	# like it did before this swap, instead of every house ballooning out to
	# cover the whole city.
	var base_scale := float(cfg["building_size"]) * 1.9 / 5.5

	# Keep at the centre, on the highest ground available - it is the thing
	# that identifies the settlement from a distance.
	var keep_basis := Basis(Vector3.UP, rng.randf_range(0.0, TAU)).scaled(
		Vector3.ONE * base_scale * 1.3
	)
	keeps.append(Transform3D(keep_basis, centre))

	# Kit roofs carry real eaves (unlike the old cone, whose radius was its
	# whole footprint), so two candidate spots that would have looked fine as
	# flat-shaded cones now read as interpenetrating rooftops. A minimum
	# spacing - checked only within this city, so it stays a cheap O(n^2) over
	# building_count rather than needing a spatial grid - keeps some of that
	# TW-style eave-touching density without the pinwheel of clipped roofs it
	# produced unchecked. 0.75x the roof's own diameter: tight enough to still
	# read as a packed old town, loose enough that most candidates survive.
	var roof_diameter := base_scale * 5.5
	var min_building_spacing := roof_diameter * 0.75
	var placed_positions: Array[Vector2] = []

	for _b in building_count:
		# sqrt of a uniform sample spreads points evenly by area; without it
		# everything piles into the centre. Biased slightly inward on top of
		# that so the core still reads as denser than the outskirts.
		var t := sqrt(rng.randf())
		t = pow(t, 1.25)
		var angle := rng.randf_range(0.0, TAU)
		var r := t * radius * 0.88
		var x := centre.x + cos(angle) * r
		var z := centre.z + sin(angle) * r

		var h: float = terrain_builder.height_at(x, z)
		if not is_finite(h):
			continue
		var slope: float = terrain_builder.slope_at(x, z)
		if not is_finite(slope) or slope > max_building_slope:
			continue

		var pos := Vector2(x, z)
		var too_close := false
		for existing in placed_positions:
			if existing.distance_to(pos) < min_building_spacing:
				too_close = true
				break
		if too_close:
			continue
		placed_positions.append(pos)

		# Fully random yaw. The old procedural roof was a symmetric 4-sided
		# cone, so biasing yaw to face the centre cost nothing and kept the
		# cluster from reading as scattered debris; these kit roofs are
		# rectangular, and that same bias lines every roof's long axis up
		# radially, which reads as a pinwheel spoking out from the keep -
		# a much worse artifact than a bit of genuine randomness.
		var facing := rng.randf_range(0.0, TAU)
		var basis := Basis(Vector3.UP, facing).scaled(
			Vector3.ONE * base_scale * rng.randf_range(0.8, 1.25)
		)
		var variant := rng.randi_range(0, variant_count - 1)
		(houses[variant] as Array[Transform3D]).append(Transform3D(basis, Vector3(x, h, z)))

	if not bool(cfg["wall_enabled"]):
		return

	var segments := int(cfg["wall_segments"])
	var tower_every := int(cfg["wall_tower_every"])
	for s in segments:
		var angle := TAU * float(s) / float(segments)
		var x := centre.x + cos(angle) * radius
		var z := centre.z + sin(angle) * radius
		var h: float = terrain_builder.height_at(x, z)
		if not is_finite(h):
			continue
		# The segment's long axis is local +X, and a Y-rotation of theta sends
		# +X to (cos theta, 0, -sin theta). To lay that along the tangent
		# (-sin angle, 0, cos angle) theta must be -(angle + PI/2); plain
		# -angle points every segment radially outward, so the wall comes out
		# as a ring of spokes rather than a curtain.
		var basis := Basis(Vector3.UP, -(angle + PI * 0.5))
		walls.append(Transform3D(basis, Vector3(x, h, z)))
		if tower_every > 0 and s % tower_every == 0:
			towers.append(Transform3D(basis, Vector3(x, h, z)))


func _add_multimesh(node_name: String, mesh: Mesh, transforms: Array[Transform3D]) -> void:
	if transforms.is_empty():
		return
	var multimesh := MultiMesh.new()
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	multimesh.mesh = mesh
	multimesh.instance_count = transforms.size()
	for i in transforms.size():
		multimesh.set_instance_transform(i, transforms[i])

	var node := MultiMeshInstance3D.new()
	node.name = node_name
	node.multimesh = multimesh
	add_child(node)


func _material(color: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.9
	mat.specular_mode = BaseMaterial3D.SPECULAR_DISABLED
	return mat


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("cities.%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))


## Loads one kit piece and returns its mesh, caching by name. Returns null
## (and records an error) if the kit hasn't been vendored - `make buildings`
## populates assets/buildings/kit/ from a cached Quaternius download, see
## tools/fetch_buildings.py.
func _load_piece(piece_name: String) -> Mesh:
	if _piece_cache.has(piece_name):
		return _piece_cache[piece_name]

	var path := KIT_DIR + piece_name + ".gltf"
	if not ResourceLoader.exists(path):
		errors.append(
			"cities: missing building piece %s - run `make buildings` to vendor the kit" % path
		)
		_piece_cache[piece_name] = null
		return null

	var scene: PackedScene = load(path)
	var instance := scene.instantiate()
	var mesh := _find_mesh(instance)
	instance.queue_free()
	if mesh == null:
		errors.append("cities: %s has no MeshInstance3D" % path)
	_piece_cache[piece_name] = mesh
	return mesh


func _find_mesh(node: Node) -> Mesh:
	if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
		return (node as MeshInstance3D).mesh
	for child in node.get_children():
		var found := _find_mesh(child)
		if found != null:
			return found
	return null


## Glues a recipe (piece name -> local transform) into one ArrayMesh, one
## surface per piece so each keeps its own kit material. This is what makes a
## whole building variant a single MultiMesh draw call instead of one call
## per piece per instance.
func _merge_pieces(recipe: Array) -> ArrayMesh:
	var mesh := ArrayMesh.new()
	for entry in recipe:
		var piece_name: String = entry[0]
		var xform: Transform3D = entry[1]
		var src := _load_piece(piece_name)
		if src == null:
			continue
		for surf in src.get_surface_count():
			var st := SurfaceTool.new()
			st.begin(Mesh.PRIMITIVE_TRIANGLES)
			st.append_from(src, surf, xform)
			var mat := src.surface_get_material(surf)
			if mat != null:
				st.set_material(mat)
			st.commit(mesh)
	return mesh


## Four walls around one 2m floor tile, in local house space. `door_side`
## picks which of the four gets a doorway; the rest are blank. Front faces
## point outward per side: +Z=north (unrotated, the piece's natural
## orientation), then +X/-Z/-X follow by quarter turns - matches the tangent
## convention _lay_out_city already uses for the curtain wall.
func _walled_tile(wall_piece: String, door_piece: String, door_side: int) -> Array:
	var sides := [
		["north", 0.0, Vector3(0.0, 0.0, TILE_HALF)],
		["east", PI * 0.5, Vector3(TILE_HALF, 0.0, 0.0)],
		["south", PI, Vector3(0.0, 0.0, -TILE_HALF)],
		["west", -PI * 0.5, Vector3(-TILE_HALF, 0.0, 0.0)],
	]
	var parts := []
	for i in sides.size():
		var piece := door_piece if i == door_side else wall_piece
		var basis := Basis(Vector3.UP, sides[i][1])
		parts.append([piece, Transform3D(basis, sides[i][2])])
	return parts


## Roof piece centred on a tile, eave resting on `top` (the wall height it
## sits above).
func _roof_at(roof_piece: String, top: float) -> Array:
	var drop: float = ROOF_EAVE_DROP.get(roof_piece, 0.5)
	return [[roof_piece, Transform3D(Basis(), Vector3(0.0, top + drop, 0.0))]]


## Three house variants sharing the same modular grammar (floor, four walls,
## roof) but different kit materials/footprints, so a city reads as a mix of
## buildings instead of one shape repeated - the whole point of moving off
## the procedural box.
func _house_recipes() -> Array:
	var recipes := []

	var a := []
	a.append(["Floor_WoodDark", Transform3D()])
	a.append_array(_walled_tile("Wall_Plaster_Straight", "Wall_Plaster_Door_Flat", 0))
	a.append_array(_roof_at("Roof_RoundTiles_4x4", WALL_TOP))
	a.append(
		["Prop_Chimney", Transform3D(Basis(), Vector3(TILE_HALF * 0.6, WALL_TOP, TILE_HALF * 0.6))]
	)
	recipes.append(a)

	var b := []
	b.append(["Floor_RedBrick", Transform3D()])
	b.append_array(_walled_tile("Wall_UnevenBrick_Straight", "Wall_UnevenBrick_Door_Flat", 2))
	b.append_array(_roof_at("Roof_RoundTiles_4x6", WALL_TOP))
	recipes.append(b)

	var c := []
	c.append(["Floor_UnevenBrick", Transform3D()])
	c.append_array(_walled_tile("Wall_Plaster_Straight", "Wall_Plaster_Door_Round", 1))
	c.append_array(_roof_at("Roof_RoundTiles_4x4", WALL_TOP))
	c.append(
		[
			"Prop_Chimney2",
			Transform3D(Basis(), Vector3(-TILE_HALF * 0.6, WALL_TOP, -TILE_HALF * 0.6))
		]
	)
	recipes.append(c)

	return recipes


## Two storeys of the same tile stacked, topped with the kit's conical tower
## roof - taller and more ornamented than any house, so it still reads as the
## settlement's landmark the way the old size*2.2 box did.
func _keep_recipe() -> Array:
	var recipe := []
	recipe.append(["Floor_Brick", Transform3D()])
	recipe.append_array(_walled_tile("Wall_UnevenBrick_Straight", "Wall_UnevenBrick_Door_Flat", 0))
	recipe.append(["Floor_Brick", Transform3D(Basis(), Vector3(0.0, WALL_TOP, 0.0))])
	for entry in _walled_tile("Wall_Plaster_Straight", "Wall_Plaster_Window_Wide_Flat", 2):
		var piece: String = entry[0]
		var xform: Transform3D = entry[1]
		recipe.append([piece, Transform3D(xform.basis, xform.origin + Vector3(0.0, WALL_TOP, 0.0))])
	recipe.append_array(_roof_at("Roof_Tower_RoundTiles", WALL_TOP * 2.0))
	return recipe


## Curtain wall: one long low box per segment. Segments are wider than the arc
## they span so consecutive ones overlap into a continuous ring instead of a
## dashed line. Procedural, not kit-built: the pack has no fortification
## pieces, and a plain stone ring reads fine on its own at campaign zoom.
func _make_curtain(cfg: Dictionary, size: float) -> ArrayMesh:
	var body := BoxMesh.new()
	body.size = Vector3(size * 2.6, size * 1.0, size * 0.42)

	var mesh := ArrayMesh.new()
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	st.append_from(body, 0, Transform3D(Basis(), Vector3(0.0, size * 0.5, 0.0)))
	st.commit(mesh)
	mesh.surface_set_material(0, _material(_color(cfg, "wall_color")))
	return mesh


## Tower, placed only every Nth segment. Breaking the wall's silhouette at
## intervals is what makes it read as fortification rather than a fence.
func _make_tower(cfg: Dictionary, size: float) -> ArrayMesh:
	var body := BoxMesh.new()
	body.size = Vector3(size * 0.85, size * 1.7, size * 0.85)

	var roof := CylinderMesh.new()
	roof.top_radius = 0.0
	roof.bottom_radius = size * 0.72
	roof.height = size * 0.7
	roof.radial_segments = 4
	roof.rings = 1

	var mesh := ArrayMesh.new()

	var body_st := SurfaceTool.new()
	body_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	body_st.append_from(body, 0, Transform3D(Basis(), Vector3(0.0, size * 0.85, 0.0)))
	body_st.commit(mesh)

	var roof_st := SurfaceTool.new()
	roof_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	roof_st.append_from(roof, 0, Transform3D(Basis(), Vector3(0.0, size * 1.7 + size * 0.35, 0.0)))
	roof_st.commit(mesh)

	mesh.surface_set_material(0, _material(_color(cfg, "wall_color")))
	mesh.surface_set_material(1, _material(_color(cfg, "roof_color")))
	return mesh
