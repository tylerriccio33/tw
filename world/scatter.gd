extends Node3D

## Forests, as GPU-instanced trees.
##
## Placement follows the approach proven in the earlier version of this project
## (`bake/src/geom/forests.rs` at ceccad9): a low-frequency density mask decides
## *where* woods cluster, and every candidate then passes the same
## height/slope gate so trees can never wade into the surf or climb onto bare
## peaks. Jitter comes from a seeded RNG rather than per-frame randomness, so a
## rebuild reproduces the same wood instead of reshuffling it.

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

## Populated for reuse by later stages - cities avoid dropping a settlement in
## the middle of dense woodland.
var tree_positions: PackedVector3Array = []


func build(
	cfg: Dictionary, seed_value: int, map_extent: float, terrain_builder: Node
) -> void:
	var spacing := float(cfg["spacing"])
	if spacing <= 0.0:
		errors.append("forests.spacing must be > 0, got %f" % spacing)
		return

	var min_height := float(cfg["min_height"])
	var max_height := float(cfg["max_height"])
	if min_height >= max_height:
		errors.append("forests.min_height (%f) must be below max_height (%f)"
			% [min_height, max_height])
		return

	var max_slope := float(cfg["max_slope_degrees"])
	var jitter := float(cfg["jitter"])
	var density_threshold := float(cfg["density_threshold"])
	var scale_min := float(cfg["scale_min"])
	var scale_max := float(cfg["scale_max"])
	var conifer_fraction := float(cfg["conifer_height_fraction"])

	var density := FastNoiseLite.new()
	density.seed = seed_value + 505
	density.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	density.frequency = float(cfg["density_frequency"])
	density.fractal_octaves = 3

	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value + 606

	# Conifers take the high ground, broadleaf the lowlands - the altitude
	# split is most of what makes forest cover read as varied from above.
	var conifer_transforms: Array[Transform3D] = []
	var broadleaf_transforms: Array[Transform3D] = []

	var conifer_line := min_height + (max_height - min_height) * conifer_fraction

	var steps := int(map_extent * 2.0 / spacing)
	for iz in steps:
		for ix in steps:
			var x := -map_extent + (float(ix) + 0.5) * spacing
			var z := -map_extent + (float(iz) + 0.5) * spacing

			# Density mask first: it rejects most candidates for the price of
			# one noise lookup, before any terrain sampling.
			var d := density.get_noise_2d(x, z) * 0.5 + 0.5
			if d < density_threshold:
				continue

			x += rng.randf_range(-jitter, jitter) * spacing * 0.5
			z += rng.randf_range(-jitter, jitter) * spacing * 0.5

			# Explicitly typed: terrain_builder arrives as an untyped Node
			# (typing it would be a cyclic preload), so these come back Variant.
			var h: float = terrain_builder.height_at(x, z)
			if not is_finite(h) or h < min_height or h > max_height:
				continue
			var slope: float = terrain_builder.slope_at(x, z)
			if not is_finite(slope) or slope > max_slope:
				continue

			# Thin the wood towards its edge so clusters have soft boundaries
			# instead of a hard line where the mask crosses the threshold.
			var edge := clampf((d - density_threshold) / 0.18, 0.0, 1.0)
			if rng.randf() > 0.35 + 0.65 * edge:
				continue

			var basis := Basis(Vector3.UP, rng.randf_range(0.0, TAU)).scaled(
				Vector3.ONE * rng.randf_range(scale_min, scale_max))
			var xform := Transform3D(basis, Vector3(x, h, z))
			if h >= conifer_line:
				conifer_transforms.append(xform)
			else:
				broadleaf_transforms.append(xform)
			tree_positions.append(Vector3(x, h, z))

	if conifer_transforms.is_empty() and broadleaf_transforms.is_empty():
		errors.append(
			"forest scatter placed 0 trees - check forests.density_threshold "
			+ "(%f) and the height gate %f..%f against the terrain height range"
			% [density_threshold, min_height, max_height]
		)
		return

	var tree_height := float(cfg["tree_height"])
	_add_multimesh("Conifers", _make_conifer(cfg, tree_height), conifer_transforms)
	_add_multimesh("Broadleaf", _make_broadleaf(cfg, tree_height), broadleaf_transforms)

	built = true


## Tree count, for the harness's per-render stats block.
func stats() -> Dictionary:
	return {"trees": tree_positions.size()}


func _add_multimesh(node_name: String, mesh: Mesh, transforms: Array[Transform3D]) -> void:
	if transforms.is_empty():
		return
	var multimesh := MultiMesh.new()
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	multimesh.mesh = mesh
	# instance_count allocates the buffer, so it must be set before any
	# set_instance_transform call or the writes go nowhere.
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
	mat.roughness = 0.95
	mat.specular_mode = BaseMaterial3D.SPECULAR_DISABLED
	return mat


## Trunk plus canopy as one two-surface mesh, so a whole species is a single
## MultiMesh and therefore a single draw call.
func _assemble(trunk: Mesh, canopy: Mesh, trunk_color: Color, canopy_color: Color,
		trunk_y: float, canopy_y: float) -> ArrayMesh:
	var mesh := ArrayMesh.new()

	var trunk_st := SurfaceTool.new()
	trunk_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	trunk_st.append_from(trunk, 0, Transform3D(Basis(), Vector3(0.0, trunk_y, 0.0)))
	trunk_st.commit(mesh)

	var canopy_st := SurfaceTool.new()
	canopy_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	canopy_st.append_from(canopy, 0, Transform3D(Basis(), Vector3(0.0, canopy_y, 0.0)))
	canopy_st.commit(mesh)

	mesh.surface_set_material(0, _material(trunk_color))
	mesh.surface_set_material(1, _material(canopy_color))
	return mesh


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("forests.%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))


func _make_conifer(cfg: Dictionary, height: float) -> ArrayMesh:
	var trunk := CylinderMesh.new()
	trunk.top_radius = height * 0.035
	trunk.bottom_radius = height * 0.05
	trunk.height = height * 0.35
	trunk.radial_segments = 5
	trunk.rings = 1

	var canopy := CylinderMesh.new()
	canopy.top_radius = 0.0
	canopy.bottom_radius = height * 0.30
	canopy.height = height * 0.85
	canopy.radial_segments = 7
	canopy.rings = 1

	return _assemble(
		trunk, canopy,
		_color(cfg, "trunk_color"), _color(cfg, "conifer_color"),
		height * 0.175, height * 0.35 + height * 0.425
	)


func _make_broadleaf(cfg: Dictionary, height: float) -> ArrayMesh:
	var trunk := CylinderMesh.new()
	trunk.top_radius = height * 0.045
	trunk.bottom_radius = height * 0.06
	trunk.height = height * 0.45
	trunk.radial_segments = 5
	trunk.rings = 1

	var canopy := SphereMesh.new()
	canopy.radius = height * 0.33
	canopy.height = height * 0.60
	canopy.radial_segments = 7
	canopy.rings = 4

	return _assemble(
		trunk, canopy,
		_color(cfg, "trunk_color"), _color(cfg, "broadleaf_color"),
		height * 0.225, height * 0.45 + height * 0.22
	)
