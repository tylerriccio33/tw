extends Node3D

## Forests, as GPU-instanced alpha-card trees.
##
## Placement follows the approach proven in the earlier version of this project
## (`bake/src/geom/forests.rs` at ceccad9): a low-frequency density mask decides
## *where* woods cluster, and every candidate then passes the same
## height/slope gate so trees can never wade into the surf or climb onto bare
## peaks. Jitter comes from a seeded RNG rather than per-frame randomness, so a
## rebuild reproduces the same wood instead of reshuffling it.
##
## The trees themselves are a trunk cylinder plus a few crossed quads carrying
## Poly Haven's photoscanned foliage maps (see tools/fetch_foliage.py for why
## the source meshes are not used). That is ~100 triangles a tree instead of
## the millions a photoscan costs, which is what makes ~4500 of them viable.

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

## Populated for reuse by later stages - cities avoid dropping a settlement in
## the middle of dense woodland.
var tree_positions: PackedVector3Array = []

## Species directories under assets/foliage/, split by the altitude band they
## belong to. Conifers take the high ground, broadleaf the lowlands - the
## altitude split is most of what makes forest cover read as varied from above.
const CONIFER_SPECIES := ["conifer_fir", "conifer_sapling"]
const BROADLEAF_SPECIES := ["broadleaf_island", "broadleaf_island_02", "broadleaf_jacaranda"]

var _species_counts: Dictionary = {}


func build(cfg: Dictionary, seed_value: int, map_extent: float, terrain_builder: Node) -> void:
	var spacing := float(cfg["spacing"])
	if spacing <= 0.0:
		errors.append("forests.spacing must be > 0, got %f" % spacing)
		return

	var min_height := float(cfg["min_height"])
	var max_height := float(cfg["max_height"])
	if min_height >= max_height:
		errors.append(
			"forests.min_height (%f) must be below max_height (%f)" % [min_height, max_height]
		)
		return

	var max_slope := float(cfg["max_slope_degrees"])
	var jitter := float(cfg["jitter"])
	var density_threshold := float(cfg["density_threshold"])
	var scale_min := float(cfg["scale_min"])
	var scale_max := float(cfg["scale_max"])
	var conifer_fraction := float(cfg["conifer_height_fraction"])
	var conifer_blend := float(cfg["conifer_blend_fraction"])
	var stand_threshold := float(cfg["conifer_stand_threshold"])
	var tint_jitter := float(cfg["tint_jitter"])

	var density := FastNoiseLite.new()
	density.seed = seed_value + 505
	density.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	density.frequency = float(cfg["density_frequency"])
	density.fractal_octaves = 3

	# Separate, lower-frequency field than the density mask: it decides *which*
	# species grows where, so conifers form stands rather than being sprinkled
	# one-in-N through a broadleaf wood.
	var species_noise := FastNoiseLite.new()
	species_noise.seed = seed_value + 707
	species_noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	species_noise.frequency = float(cfg["conifer_stand_frequency"])

	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value + 606

	# Transforms are bucketed per species *and* per grid cell (buckets[species]
	# is a Dictionary keyed by cell Vector2i) rather than one flat array per
	# species. A single MultiMesh spanning the whole map is either entirely
	# on-screen or entirely off - Godot's visibility_range culls a node by its
	# own AABB, so it cannot drop just the far side of one giant mesh. Splitting
	# into cells gives each cell its own small AABB, so visibility_range_end
	# below actually stops distant cells from being drawn (or even traversed by
	# the renderer) as the camera pans/zooms, instead of paying for all ~4500
	# trees every frame regardless of where the camera is looking. Colours ride
	# alongside in the same per-cell arrays.
	var lod_cell_size := float(cfg["lod_cell_size"])
	var buckets: Dictionary = {}
	var tints: Dictionary = {}
	for species in CONIFER_SPECIES + BROADLEAF_SPECIES:
		buckets[species] = {}
		tints[species] = {}

	var conifer_line := min_height + (max_height - min_height) * conifer_fraction
	var conifer_band := (max_height - min_height) * conifer_blend

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
				Vector3.ONE * rng.randf_range(scale_min, scale_max)
			)
			# Sink each tree slightly so the card bases are buried rather than
			# floating on a hairline of terrain when the ground slopes away.
			var xform := Transform3D(basis, Vector3(x, h - 0.6, z))

			# A hard `h >= conifer_line` split put 97% of the wood in the
			# broadleaf pool: the conifer line sits at the top of the height
			# range, where the slope gate has already rejected almost every
			# candidate, so the high-altitude species were effectively unused.
			# Altitude still decides the treeline stands, but a low-frequency
			# species field also seeds conifer stands down in the lowlands, the
			# way a real mixed forest is patchy rather than layered.
			var altitude_bias := smoothstep(
				conifer_line - conifer_band, conifer_line + conifer_band, h
			)
			var stand := species_noise.get_noise_2d(x, z) * 0.5 + 0.5
			var conifer_chance := maxf(
				altitude_bias, smoothstep(stand_threshold, stand_threshold + 0.12, stand)
			)
			var pool: Array = CONIFER_SPECIES if rng.randf() < conifer_chance else BROADLEAF_SPECIES
			var species: String = pool[rng.randi_range(0, pool.size() - 1)]

			var cell := Vector2i(int(floor(x / lod_cell_size)), int(floor(z / lod_cell_size)))
			var species_cells: Dictionary = buckets[species]
			var species_tints: Dictionary = tints[species]
			if not species_cells.has(cell):
				species_cells[cell] = ([] as Array[Transform3D])
				species_tints[cell] = ([] as Array[Color])
			(species_cells[cell] as Array[Transform3D]).append(xform)
			# Per-instance tint, multiplied over the photo albedo. Without it
			# every tree of a species is the same colour and a wood reads as
			# one stamp repeated, which no amount of silhouette variation hides.
			(species_tints[cell] as Array[Color]).append(
				Color.WHITE.lerp(
					Color(
						rng.randf_range(0.78, 1.12),
						rng.randf_range(0.86, 1.14),
						rng.randf_range(0.72, 1.05)
					),
					tint_jitter
				)
			)
			tree_positions.append(Vector3(x, h, z))

	if tree_positions.is_empty():
		errors.append(
			(
				"forest scatter placed 0 trees - check forests.density_threshold "
				+ (
					"(%f) and the height gate %f..%f against the terrain height range"
					% [density_threshold, min_height, max_height]
				)
			)
		)
		return

	var tree_height := float(cfg["tree_height"])
	var view_distance := float(cfg["view_distance"])
	var view_distance_margin := float(cfg["view_distance_margin"])
	for species in buckets:
		var species_cells: Dictionary = buckets[species]
		if species_cells.is_empty():
			continue
		var mesh := _make_tree(species, tree_height, cfg)
		if mesh == null:
			return
		var species_count := 0
		for cell in species_cells:
			var transforms: Array[Transform3D] = species_cells[cell]
			if transforms.is_empty():
				continue
			var colors: Array[Color] = tints[species][cell]
			_add_multimesh(
				"%s_%d_%d" % [species, cell.x, cell.y],
				mesh,
				transforms,
				colors,
				view_distance,
				view_distance_margin
			)
			species_count += transforms.size()
		_species_counts[species] = species_count

	if not errors.is_empty():
		return

	built = true


## Tree counts, for the harness's per-render stats block. The per-species
## breakdown rides along because a single total looks healthy even when one
## species failed to load and its whole altitude band went bare.
func stats() -> Dictionary:
	return {"trees": tree_positions.size(), "tree_species": _species_counts}


func _add_multimesh(
	node_name: String,
	mesh: Mesh,
	transforms: Array[Transform3D],
	colors: Array[Color],
	view_distance: float,
	view_distance_margin: float
) -> void:
	var multimesh := MultiMesh.new()
	multimesh.transform_format = MultiMesh.TRANSFORM_3D
	# Must be set before instance_count: it widens the per-instance buffer, and
	# enabling it afterwards discards whatever was already written.
	multimesh.use_colors = true
	multimesh.mesh = mesh
	# instance_count allocates the buffer, so it must be set before any
	# set_instance_transform call or the writes go nowhere.
	multimesh.instance_count = transforms.size()
	for i in transforms.size():
		multimesh.set_instance_transform(i, transforms[i])
		multimesh.set_instance_color(i, colors[i])

	var node := MultiMeshInstance3D.new()
	node.name = node_name
	node.multimesh = multimesh
	# Canopy cards are thin and double-sided; without this they wink out as
	# the shadow pass culls them edge-on and the wood loses its ground shadow.
	node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_DOUBLE_SIDED
	# Each node is one grid cell's worth of one species (see the bucket comment
	# in build()), so this AABB is small enough that visibility_range_end
	# actually drops whole cells past view_distance instead of an all-or-
	# nothing decision over the entire forest. The margin cross-fades the cut
	# so cells don't hard-pop as the camera pans/zooms past the threshold.
	if view_distance > 0.0:
		node.visibility_range_end = view_distance
		node.visibility_range_end_margin = view_distance_margin
		node.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_SELF
	add_child(node)


func _load_map(species: String, map_name: String) -> Texture2D:
	var path := "res://assets/foliage/%s/%s.png" % [species, map_name]
	if not ResourceLoader.exists(path):
		errors.append("could not find %s - run `make foliage` to vendor tree textures" % path)
		return null
	return load(path)


## Canopy cards: alpha-scissored rather than alpha-blended. Blending would need
## back-to-front sorting that thousands of interpenetrating quads can never
## satisfy, and it disables depth write, so the wood would visibly tear against
## itself. Scissor keeps them opaque and order-independent.
func _canopy_material(species: String) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _load_map(species, "canopy_albedo")
	mat.normal_texture = _load_map(species, "canopy_normal")
	mat.normal_enabled = mat.normal_texture != null

	# One ARM texture drives three channels; Godot selects per-property rather
	# than needing the map split into three files.
	var arm := _load_map(species, "canopy_arm")
	mat.ao_texture = arm
	mat.ao_enabled = arm != null
	mat.ao_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_RED
	mat.roughness_texture = arm
	mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN

	# Lets the MultiMesh's per-instance colour reach albedo; without it every
	# set_instance_color above is written and then ignored.
	mat.vertex_color_use_as_albedo = true

	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
	mat.alpha_scissor_threshold = 0.5
	# Antialiasing on the scissor edge, otherwise every leaf boundary crawls
	# under TAA rather than resolving.
	mat.alpha_antialiasing_mode = BaseMaterial3D.ALPHA_ANTIALIASING_ALPHA_TO_COVERAGE_AND_TO_ONE
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	# Foliage is thin and lets light through. Without this, canopies read as
	# solid black cutouts whenever the sun is behind them.
	mat.backlight_enabled = true
	mat.backlight = Color(0.16, 0.22, 0.10)
	return mat


func _bark_material(species: String) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = _load_map(species, "bark_albedo")
	mat.normal_texture = _load_map(species, "bark_normal")
	mat.normal_enabled = mat.normal_texture != null

	var arm := _load_map(species, "bark_arm")
	mat.ao_texture = arm
	mat.ao_enabled = arm != null
	mat.ao_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_RED
	mat.roughness_texture = arm
	mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
	return mat


## Trunk plus crossed canopy cards as one two-surface mesh, so a whole species
## is a single MultiMesh and therefore a single draw call.
##
## Conifers get taller, narrower, vertically-stacked cards (a spire); broadleaf
## get wider cards clustered near the top (a dome). The silhouette difference
## is what stops a mixed wood reading as one repeated stamp.
func _make_tree(species: String, height: float, cfg: Dictionary) -> ArrayMesh:
	var is_conifer := species in CONIFER_SPECIES

	var trunk_height := height * (0.42 if is_conifer else 0.5)
	var trunk := CylinderMesh.new()
	trunk.top_radius = height * 0.022
	trunk.bottom_radius = height * 0.045
	trunk.height = trunk_height
	trunk.radial_segments = 6
	trunk.rings = 1

	var mesh := ArrayMesh.new()

	var trunk_st := SurfaceTool.new()
	trunk_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	trunk_st.append_from(trunk, 0, Transform3D(Basis(), Vector3(0.0, trunk_height * 0.5, 0.0)))
	trunk_st.commit(mesh)

	var canopy_st := SurfaceTool.new()
	canopy_st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_build_canopy_cards(canopy_st, height, trunk_height, is_conifer, species, cfg)
	canopy_st.generate_normals()
	canopy_st.generate_tangents()
	canopy_st.commit(mesh)

	if not errors.is_empty():
		return null

	mesh.surface_set_material(0, _bark_material(species))
	mesh.surface_set_material(1, _canopy_material(species))
	if not errors.is_empty():
		return null
	return mesh


## Loads the leaf-cell rects that tools/fetch_foliage.py segmented out of the
## atlas. Without these a card would stretch the whole sheet - which is a grid
## of separate leaves, not a canopy - across itself and the tree renders as a
## few enormous floating leaves on a bare stick.
func _load_cells(species: String) -> Array:
	var path := "res://assets/foliage/%s/cells.json" % species
	if not FileAccess.file_exists(path):
		errors.append("could not find %s - run `make foliage` to vendor tree textures" % path)
		return []
	var json := JSON.new()
	if json.parse(FileAccess.get_file_as_string(path)) != OK:
		errors.append("invalid JSON in %s: %s" % [path, json.get_error_message()])
		return []
	if typeof(json.data) != TYPE_ARRAY or (json.data as Array).is_empty():
		errors.append("%s contained no leaf cells" % path)
		return []
	return json.data


## Scatters many small single-leaf cards over a canopy shell - a cone for
## conifers, a squashed sphere for broadleaf. Each card samples one segmented
## cell of the atlas and is sized by that cell's own aspect ratio, so a long
## jacaranda frond stays long and a fir twig stays stubby.
func _build_canopy_cards(
	st: SurfaceTool,
	height: float,
	trunk_height: float,
	is_conifer: bool,
	species: String,
	cfg: Dictionary
) -> void:
	var cells := _load_cells(species)
	if cells.is_empty():
		return

	# Seeded off the species name so a given species always builds the same
	# mesh, independent of iteration order or how many trees got placed.
	var rng := RandomNumberGenerator.new()
	rng.seed = hash(species)

	var canopy_cfg: Dictionary = cfg["canopy"]
	var kind := "conifer" if is_conifer else "broadleaf"
	var canopy_radius := height * float(canopy_cfg["%s_radius_fraction" % kind])
	var leaf_size := height * float(canopy_cfg["%s_leaf_fraction" % kind])
	var card_count := int(canopy_cfg["%s_cards" % kind])

	# The canopy starts partway *down* the trunk rather than on top of it.
	# Starting at trunk_height left a bare pole under every conifer - the most
	# obvious tell in the coast shot - because a fir's foliage in reality runs
	# most of the way to the ground.
	var canopy_base := trunk_height * float(canopy_cfg["%s_base_fraction" % kind])
	var canopy_height := height - canopy_base

	for i in card_count:
		var cell: Dictionary = cells[rng.randi_range(0, cells.size() - 1)]
		var cw := float(cell["w"])
		var ch := float(cell["h"])
		if cw <= 0.0 or ch <= 0.0:
			continue

		# Distribute up the canopy with a slight bias towards the top, where
		# real foliage mass sits.
		var t := pow(rng.randf(), 0.75)
		var y := canopy_base + canopy_height * (0.05 + 0.9 * t)

		# Cone tapers to the tip; the broadleaf shell bulges in the middle.
		var shell: float
		if is_conifer:
			shell = 1.0 - 0.85 * t
		else:
			shell = sin(PI * clampf(0.18 + 0.82 * t, 0.0, 1.0))

		var yaw := rng.randf_range(0.0, TAU)
		var radius := canopy_radius * shell * sqrt(rng.randf())
		var centre := Vector3(cos(yaw) * radius, y, sin(yaw) * radius)

		# Aspect-correct: keep the leaf's real proportions rather than forcing
		# every cell into a square.
		var aspect := cw / ch
		var half_h := leaf_size * rng.randf_range(0.75, 1.25) * 0.5
		var half_w := half_h * aspect

		# Cards face outward from the trunk and tilt down, the way a branch
		# hangs - and never sit axis-aligned, so none is exactly edge-on to a
		# fixed shot camera.
		var facing := yaw + rng.randf_range(-0.6, 0.6)
		var tilt := rng.randf_range(-0.55, -0.05) if not is_conifer else rng.randf_range(-0.4, 0.1)
		_add_card(st, centre, facing, tilt, half_w, half_h, cell)


func _add_card(
	st: SurfaceTool,
	centre: Vector3,
	yaw: float,
	tilt: float,
	half_w: float,
	half_h: float,
	cell: Dictionary
) -> void:
	var basis := Basis(Vector3.UP, yaw) * Basis(Vector3.RIGHT, tilt)
	var right := basis * Vector3(half_w, 0.0, 0.0)
	var up := basis * Vector3(0.0, half_h, 0.0)

	var bl := centre - right - up
	var br := centre + right - up
	var tr := centre + right + up
	var tl := centre - right + up

	# UVs address just this leaf's rect in the atlas rather than the whole
	# sheet. v is flipped because image space runs top-down.
	var u0 := float(cell["x"])
	var v0 := float(cell["y"])
	var u1 := u0 + float(cell["w"])
	var v1 := v0 + float(cell["h"])

	# Two triangles, wound so the front face points along the card's +Z. The
	# material is cull-disabled anyway, but consistent winding keeps the
	# generated normals from cancelling out across the quad.
	st.set_uv(Vector2(u0, v1))
	st.add_vertex(bl)
	st.set_uv(Vector2(u1, v1))
	st.add_vertex(br)
	st.set_uv(Vector2(u1, v0))
	st.add_vertex(tr)

	st.set_uv(Vector2(u0, v1))
	st.add_vertex(bl)
	st.set_uv(Vector2(u1, v0))
	st.add_vertex(tr)
	st.set_uv(Vector2(u0, v0))
	st.add_vertex(tl)
