extends Node3D

## Settlements: clusters of buildings with an optional wall ring.
##
## Readability beats fidelity here. At campaign-map zoom a city is recognised
## by its silhouette and its clearing, not by architecture, so this builds
## simple massing - a dense core thinning outwards, a keep at the centre, and
## a wall that makes the edge legible against the terrain.

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

## City centres in world space, published for the road network in splines.gd.
var city_centres: PackedVector3Array = []

## Radius of each city, index-aligned with `city_centres`. Roads stop at the
## wall rather than driving through the middle of the buildings.
var city_radii: PackedFloat32Array = []


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

	var house_transforms: Array[Transform3D] = []
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
			house_transforms,
			wall_transforms,
			tower_transforms,
			keep_transforms
		)

	var building_size := float(cfg["building_size"])
	_add_multimesh("Houses", _make_house(cfg, building_size), house_transforms)
	_add_multimesh("Keeps", _make_keep(cfg, building_size), keep_transforms)
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
	houses: Array[Transform3D],
	walls: Array[Transform3D],
	towers: Array[Transform3D],
	keeps: Array[Transform3D],
) -> void:
	var building_count := int(cfg["building_count"])
	var max_building_slope := float(cfg["max_building_slope_degrees"])

	# Keep at the centre, on the highest ground available - it is the thing
	# that identifies the settlement from a distance.
	var keep_basis := Basis(Vector3.UP, rng.randf_range(0.0, TAU))
	keeps.append(Transform3D(keep_basis, centre))

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

		# Buildings face roughly towards the centre, with slack. Fully random
		# yaw reads as scattered debris rather than a settlement.
		var facing := atan2(centre.x - x, centre.z - z) + rng.randf_range(-0.5, 0.5)
		var basis := Basis(Vector3.UP, facing).scaled(Vector3.ONE * rng.randf_range(0.8, 1.25))
		houses.append(Transform3D(basis, Vector3(x, h, z)))

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


## Body plus roof as one two-surface mesh, so each building type is a single
## MultiMesh draw call.
func _assemble(
	body: Mesh, roof: Mesh, body_color: Color, roof_color: Color, body_y: float, roof_y: float
) -> ArrayMesh:
	var mesh := ArrayMesh.new()

	var body_st := SurfaceTool.new()
	body_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	body_st.append_from(body, 0, Transform3D(Basis(), Vector3(0.0, body_y, 0.0)))
	body_st.commit(mesh)

	var roof_st := SurfaceTool.new()
	roof_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	roof_st.append_from(roof, 0, Transform3D(Basis(), Vector3(0.0, roof_y, 0.0)))
	roof_st.commit(mesh)

	mesh.surface_set_material(0, _material(body_color))
	mesh.surface_set_material(1, _material(roof_color))
	return mesh


func _make_house(cfg: Dictionary, size: float) -> ArrayMesh:
	var body := BoxMesh.new()
	body.size = Vector3(size, size * 0.75, size * 1.3)

	# A 4-sided cone is a hip roof. Rotated 45 degrees so its ridge lines up
	# with the box rather than cutting across it diagonally.
	var roof := CylinderMesh.new()
	roof.top_radius = 0.0
	roof.bottom_radius = size * 0.95
	roof.height = size * 0.6
	roof.radial_segments = 4
	roof.rings = 1

	return _assemble(
		body,
		roof,
		_color(cfg, "building_color"),
		_color(cfg, "roof_color"),
		size * 0.375,
		size * 0.75 + size * 0.3
	)


func _make_keep(cfg: Dictionary, size: float) -> ArrayMesh:
	var body := BoxMesh.new()
	body.size = Vector3(size * 2.2, size * 2.6, size * 2.2)

	var roof := CylinderMesh.new()
	roof.top_radius = 0.0
	roof.bottom_radius = size * 1.9
	roof.height = size * 1.5
	roof.radial_segments = 4
	roof.rings = 1

	return _assemble(
		body,
		roof,
		_color(cfg, "keep_color"),
		_color(cfg, "roof_color"),
		size * 1.3,
		size * 2.6 + size * 0.75
	)


## Curtain wall: one long low box per segment. Segments are wider than the arc
## they span so consecutive ones overlap into a continuous ring instead of a
## dashed line.
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

	return _assemble(
		body,
		roof,
		_color(cfg, "wall_color"),
		_color(cfg, "roof_color"),
		size * 0.85,
		size * 1.7 + size * 0.35
	)
