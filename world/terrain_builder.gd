extends Node3D

## Builds the Terrain3D node and fills its regions procedurally.
##
## Shape is deliberately not art-directed - the brief is "a blob of land with
## random mountains". What is directed is the *palette* and the silhouette:
## arid tan plains, green confined to wet low ground, grey rock on anything
## steep, snow on the peaks.

var errors: PackedStringArray = []

## Set true only by the final statement of build(). A GDScript runtime error
## aborts the running function and returns to the caller without raising, so
## an empty `errors` array does NOT mean the build succeeded - the caller must
## check this flag or a crash mid-build reads as success.
var built := false

var terrain: Terrain3D

## Terrain3D persists regions here. We regenerate every run and never call
## save_directory(), so this stays empty - but the property is mandatory and
## Terrain3D errors out if the directory does not exist.
const DATA_DIR := "res://data/terrain"

## Baked regions are cached here, one subdirectory per config+seed hash.
## Terrain regen is ~7s of every ~10s render cycle; a run that only touches an
## unrelated stage (a wall rotation, a light angle) re-does that work for
## nothing. Keyed on the inputs that actually change the heightmap/control
## map - not on debug_view, which only flips a material flag at render time.
const CACHE_ROOT := "res://data/terrain_cache"

const VALID_REGION_SIZES := [64, 128, 256, 512, 1024]

## Texture slot ids. The order here defines the ids referenced by the control
## map, and must match the order textures are registered in _build_assets().
enum {
	TEX_SAND = 0,
	TEX_TAN = 1,
	TEX_GRASS = 2,
	TEX_ROCK = 3,
	TEX_SNOW = 4,
}

## CC0 source materials (ambientCG/Polyhaven), one per texture slot. Raw maps
## live under assets/textures/<slot>/; tools/fetch_textures.py bakes them into
## the albedo.png (RGB colour, A height) and normal_rough.png (RGB normal, A
## roughness) pair that Terrain3D's asset system actually reads.
const PALETTE := {
	TEX_SAND: "sand",  # ambientCG Ground093A - pale eroded coastal sand
	TEX_TAN: "tan",  # ambientCG Ground072 - dry tan plains dirt
	TEX_GRASS: "grass",  # Polyhaven aerial_grass_rock
	TEX_ROCK: "rock",  # Polyhaven marble_cliff_03 - stratified grey cliff rock
	TEX_SNOW: "snow",  # ambientCG Snow006
}

## Terrain3D multiplies world position by this to get texture UV, and clamps
## it to [0.001, 2.0] - it is not "world units per tile", it's repeats per
## world unit. 8.0 (an attempt at "repeat every ~8 units") silently clamped to
## 2.0 and over-tiled so hard the mip chain blurred every material to a flat
## average colour. 0.1 matches Terrain3D's own default and reads as a texture
## repeating roughly every 10 world units, which stays sharp at these camera
## distances without visible seams.
const TEXTURE_UV_SCALE := 0.02

## Half the width of the generated map in world units, i.e. the terrain spans
## -map_extent..+map_extent on both axes. Water sizes itself from this.
var map_extent: float:
	get:
		return _half_extent

var _cfg: Dictionary
var _palette_cfg: Dictionary
var _half_extent: float
var _vertex_spacing: float

var _warp_noise: FastNoiseLite
var _plains_noise: FastNoiseLite
var _mountain_noise: FastNoiseLite
var _belt_noise: FastNoiseLite
var _moisture_noise: FastNoiseLite


func build(cfg: Dictionary, seed_value: int) -> void:
	_cfg = cfg
	_palette_cfg = cfg["palette"]

	var region_size := int(cfg["region_size"])
	if region_size not in VALID_REGION_SIZES:
		errors.append(
			"terrain.region_size %d invalid, must be one of %s" % [region_size, VALID_REGION_SIZES]
		)
		return

	var regions_across := int(cfg["regions_across"])
	if regions_across < 1:
		errors.append("terrain.regions_across must be >= 1, got %d" % regions_across)
		return

	var data_dir_abs := ProjectSettings.globalize_path(DATA_DIR)
	if not DirAccess.dir_exists_absolute(data_dir_abs):
		var err := DirAccess.make_dir_recursive_absolute(data_dir_abs)
		if err != OK:
			errors.append("could not create %s: %s" % [DATA_DIR, error_string(err)])
			return

	terrain = Terrain3D.new()
	terrain.name = "Terrain3D"
	add_child(terrain)

	# Every one of these must come AFTER add_child(): `data` and `material` are
	# only created once the node enters the tree, and assignments made before
	# that are silently dropped. Setting region_size early is especially nasty
	# - it falls back to the 256 default, so each 1024px heightmap silently
	# spans a 4x4 block of regions instead of one.
	terrain.region_size = region_size
	terrain.vertex_spacing = float(cfg["vertex_spacing"])
	terrain.data_directory = DATA_DIR
	# NONE keeps the terrain bounded at the region edges instead of tiling
	# noise to the horizon; the water plane fills everything beyond the coast.
	terrain.material.world_background = Terrain3DMaterial.NONE
	# Sharper texture-to-texture transitions (default 0.5 reads as a soft,
	# washed-out blend at campaign-map camera distances) and macro variation
	# to break up the flat, repeating look within a single texture slot.
	terrain.material.blend_sharpness = 0.85
	# Blends a zoomed-in detail tiling of each texture over the base tiling at
	# close range, so the source photo's actual surface detail (rock cracks,
	# pebbles) survives instead of being minified into an illegible grey blur
	# at campaign-map camera distances.
	terrain.material.dual_scaling = true
	terrain.material.enable_macro_variation = true
	terrain.material.macro_variation_slope = 0.2
	# Subtle tint variants (pure white = no-op, see lightweight.gdshader) so
	# macro variation actually shows up instead of being a silent default.
	terrain.material.macro_variation1 = Color(0.88, 0.86, 0.8)
	terrain.material.macro_variation2 = Color(1.0, 0.97, 0.9)

	if terrain.region_size != region_size:
		errors.append(
			"region_size did not apply: asked %d, got %d" % [region_size, terrain.region_size]
		)
		return

	_vertex_spacing = terrain.vertex_spacing
	var region_world_size := float(region_size) * _vertex_spacing
	_half_extent = region_world_size * float(regions_across) * 0.5

	_build_noise(seed_value)
	_build_assets()

	var expected_regions := regions_across * regions_across
	var cache_dir := _cache_dir_for(cfg, region_size, regions_across, seed_value)

	if _load_cached_regions(cache_dir, expected_regions):
		print("terrain: cache hit (%s), skipped region generation" % cache_dir.get_file())
	else:
		var half := regions_across / 2
		for rz in range(-half, regions_across - half):
			for rx in range(-half, regions_across - half):
				var origin := Vector3(
					float(rx) * region_world_size, 0.0, float(rz) * region_world_size
				)
				var heights := _generate_heights(region_size, origin)
				# import_images takes [height, control, color]; nulls are skipped.
				var height_img := Image.create_from_data(
					region_size, region_size, false, Image.FORMAT_RF, heights.to_byte_array()
				)
				terrain.data.import_images([height_img, null, null], origin, 0.0, 1.0)
				_paint_controls(heights, region_size, origin)
		_save_cached_regions(cache_dir)
		print("terrain: cache miss (%s), generated and cached regions" % cache_dir.get_file())

	if terrain.data.get_region_count() != expected_regions:
		errors.append(
			(
				"expected %d regions, Terrain3D has %d"
				% [expected_regions, terrain.data.get_region_count()]
			)
		)
		return

	terrain.data.update_maps(Terrain3DRegion.TYPE_MAX, true, true)

	_apply_debug_view()

	var range_y := terrain.data.get_height_range()
	if is_equal_approx(range_y.x, range_y.y):
		errors.append("terrain is flat (height range %s) - noise produced nothing" % range_y)
		return

	built = true


## Hashes exactly the inputs that change the baked heightmap/control map.
## debug_view is deliberately excluded so toggling it does not invalidate an
## otherwise-identical cache entry.
func _cache_dir_for(
	cfg: Dictionary, region_size: int, regions_across: int, seed_value: int
) -> String:
	var hashable := {
		"region_size": region_size,
		"vertex_spacing": float(cfg["vertex_spacing"]),
		"regions_across": regions_across,
		"sea_level": float(cfg["sea_level"]),
		"ocean_depth": float(cfg["ocean_depth"]),
		"continent": cfg["continent"],
		"plains": cfg["plains"],
		"mountains": cfg["mountains"],
		"palette": cfg["palette"],
		"seed": seed_value,
	}
	var key := str(JSON.stringify(hashable).hash())
	return "%s/%s" % [CACHE_ROOT, key]


## Loads a previously-saved cache entry if one exists and looks intact.
## Returns false (and leaves `terrain.data` untouched) on any miss, including
## a corrupt/partial entry - the caller then regenerates and re-saves it, so
## a bad cache dir self-heals instead of hard-failing the render.
func _load_cached_regions(cache_dir: String, expected_regions: int) -> bool:
	var abs_dir := ProjectSettings.globalize_path(cache_dir)
	if not DirAccess.dir_exists_absolute(abs_dir):
		return false
	if DirAccess.get_files_at(abs_dir).is_empty():
		return false
	terrain.data.load_directory(cache_dir)
	if terrain.data.get_region_count() != expected_regions:
		print(
			(
				(
					"terrain: cache at %s looked stale (expected %d regions, got %d)"
					% [cache_dir, expected_regions, terrain.data.get_region_count()]
				)
				+ " - regenerating"
			)
		)
		return false
	return true


func _save_cached_regions(cache_dir: String) -> void:
	var abs_dir := ProjectSettings.globalize_path(cache_dir)
	var err := DirAccess.make_dir_recursive_absolute(abs_dir)
	if err != OK:
		errors.append("could not create terrain cache dir %s: %s" % [cache_dir, error_string(err)])
		return
	terrain.data.save_directory(cache_dir)


## Terrain3D ships debug overlays that isolate one input at a time. `grey`
## flattens albedo so only lighting/geometry remains; `control` colours each
## vertex by its texture id. Between them they localise almost any "why is the
## terrain that colour" question in a single render.
func _apply_debug_view() -> void:
	var view := String(_cfg.get("debug_view", "none"))
	var flags := {
		"grey": "show_grey",
		"control": "show_control_texture",
		"heightmap": "show_heightmap",
		"colormap": "show_colormap",
		"checkered": "show_checkered",
		"autoshader": "show_autoshader",
	}
	if view == "none":
		return
	if not flags.has(view):
		errors.append(
			"debug.terrain_view unknown: %s (expected none or one of %s)" % [view, flags.keys()]
		)
		return
	terrain.material.set(flags[view], true)


## Region count and height range, for the harness's per-render stats block.
func stats() -> Dictionary:
	var range_y := terrain.data.get_height_range()
	return {
		"regions": terrain.data.get_region_count(),
		"height_min": range_y.x,
		"height_max": range_y.y,
	}


## Ground height at a world XZ, or NAN outside the generated regions. Every
## stage that seats objects on the terrain (forests, cities, rivers) goes
## through here so they all agree with the mesh that actually rendered.
func height_at(x: float, z: float) -> float:
	return terrain.data.get_height(Vector3(x, 0.0, z))


## Surface slope in degrees at a world XZ, by central difference. `step` should
## be at least the vertex spacing or it measures interpolation noise rather than
## real landform.
func slope_at(x: float, z: float, step: float = 6.0) -> float:
	var hl := height_at(x - step, z)
	var hr := height_at(x + step, z)
	var hd := height_at(x, z - step)
	var hu := height_at(x, z + step)
	if not (is_finite(hl) and is_finite(hr) and is_finite(hd) and is_finite(hu)):
		return NAN
	var run := 2.0 * step
	return rad_to_deg(atan(Vector2((hr - hl) / run, (hu - hd) / run).length()))


func _make_noise(seed_value: int, frequency: float, octaves: int, ridged: bool) -> FastNoiseLite:
	var noise := FastNoiseLite.new()
	noise.seed = seed_value
	noise.noise_type = FastNoiseLite.TYPE_SIMPLEX_SMOOTH
	noise.frequency = frequency
	noise.fractal_type = (FastNoiseLite.FRACTAL_RIDGED if ridged else FastNoiseLite.FRACTAL_FBM)
	noise.fractal_octaves = octaves
	return noise


func _build_noise(seed_value: int) -> void:
	var continent: Dictionary = _cfg["continent"]
	var plains: Dictionary = _cfg["plains"]
	var mountains: Dictionary = _cfg["mountains"]

	# Distinct seed offsets: sharing one seed across layers correlates them,
	# which puts every mountain range on the same axis as the coastline bulges.
	_warp_noise = _make_noise(seed_value, float(continent["warp_frequency"]), 3, false)
	_plains_noise = _make_noise(
		seed_value + 101, float(plains["frequency"]), int(plains["octaves"]), false
	)
	_mountain_noise = _make_noise(
		seed_value + 202, float(mountains["frequency"]), int(mountains["octaves"]), true
	)
	_belt_noise = _make_noise(seed_value + 303, float(mountains["belt_frequency"]), 2, false)
	_moisture_noise = _make_noise(
		seed_value + 404, float(_palette_cfg["moisture_frequency"]), 3, false
	)


## Loads a texture baked by tools/fetch_textures.py, through Godot's normal
## import pipeline (`load`, not `Image.load`) so it works the same way any
## other project texture does, exported build included. Missing files read as
## a loud error rather than a silently blank material, since a typo'd slot
## name would otherwise just render as invisible/white terrain.
func _load_packed_texture(slot: String, map_name: String) -> Texture2D:
	var path := "res://assets/textures/%s/%s.png" % [slot, map_name]
	if not ResourceLoader.exists(path):
		errors.append("could not find %s - run `make textures` to vendor ambientCG textures" % path)
		return null
	return load(path)


func _build_assets() -> void:
	var assets := Terrain3DAssets.new()
	for id in [TEX_SAND, TEX_TAN, TEX_GRASS, TEX_ROCK, TEX_SNOW]:
		var slot: String = PALETTE[id]
		var asset := Terrain3DTextureAsset.new()
		asset.id = id
		asset.albedo_texture = _load_packed_texture(slot, "albedo")
		asset.normal_texture = _load_packed_texture(slot, "normal_rough")
		asset.uv_scale = TEXTURE_UV_SCALE
		# Push normal-map depth and AO past their (flat, washed-out) defaults
		# so the packed ambientCG maps actually read as bumpy/textured at
		# campaign-map camera distances instead of near-flat color.
		asset.normal_depth = 1.4
		asset.ao_strength = 0.35
		assets.set_texture(id, asset)
	terrain.assets = assets

	if terrain.assets.get_texture_count() != PALETTE.size():
		errors.append(
			(
				"expected %d textures registered, got %d"
				% [PALETTE.size(), terrain.assets.get_texture_count()]
			)
		)


## Returns region_size^2 world-space heights in row-major order, matching the
## pixel layout Terrain3D expects from import_images.
func _generate_heights(size: int, origin: Vector3) -> PackedFloat32Array:
	var continent: Dictionary = _cfg["continent"]
	var mountains: Dictionary = _cfg["mountains"]

	var falloff_start := float(continent["falloff_start"])
	var falloff_end := float(continent["falloff_end"])
	var warp_amplitude := float(continent["warp_amplitude"])
	var plains_height := float(_cfg["plains"]["height"])
	var mountain_height := float(mountains["height"])
	var ridge_power := float(mountains["ridge_power"])
	var belt_low := float(mountains["belt_low"])
	var belt_high := float(mountains["belt_high"])
	var ocean_depth := float(_cfg["ocean_depth"])

	var heights := PackedFloat32Array()
	heights.resize(size * size)

	var i := 0
	for z in size:
		var wz := origin.z + float(z) * _vertex_spacing
		for x in size:
			var wx := origin.x + float(x) * _vertex_spacing

			# Warped radial falloff -> ragged island rather than a disc.
			var radius := Vector2(wx, wz).length() / _half_extent
			radius += _warp_noise.get_noise_2d(wx, wz) * warp_amplitude
			var land := 1.0 - smoothstep(falloff_start, falloff_end, radius)

			var plains := _plains_noise.get_noise_2d(wx, wz) * 0.5 + 0.5

			# FRACTAL_RIDGED already produces creases; the power sharpens them
			# so ridgelines stay narrow instead of turning into plateaus.
			# The clamp is load-bearing: FastNoiseLite's ridged fractal
			# overshoots [-1, 1], so without it `ridge` goes slightly negative
			# and pow(negative, 1.5) is NaN. That NaN reaches the heightmap and
			# destroys the vertex normals, which renders as black patches near
			# the peaks that survive even a flat-albedo debug view.
			var ridge := clampf(_mountain_noise.get_noise_2d(wx, wz) * 0.5 + 0.5, 0.0, 1.0)
			ridge = pow(ridge, ridge_power)

			var belt := _belt_noise.get_noise_2d(wx, wz) * 0.5 + 0.5
			belt = smoothstep(belt_low, belt_high, belt)

			var elevation := plains * plains_height + ridge * belt * mountain_height
			# Blend to seabed off the coast so the shoreline is a real slope
			# and the water plane cuts it at a believable angle.
			heights[i] = land * elevation - (1.0 - land) * ocean_depth
			i += 1

	# A single non-finite height silently corrupts the vertex normals across a
	# whole area of the mesh and shows up as unexplained black shading, so it
	# is worth one linear scan to turn that into a real error message.
	for h in heights:
		if not is_finite(h):
			errors.append(
				(
					"non-finite height generated at region origin %s - check the " % origin
					+ "noise parameters in world.json (a negative base "
					+ "under a fractional pow() is the usual cause)"
				)
			)
			break

	return heights


## Writes the control map: which texture each vertex uses, and how it blends
## into its neighbour. Terrain3D's setters take global positions and handle the
## bit packing, so we never encode the control word by hand.
func _paint_controls(heights: PackedFloat32Array, size: int, origin: Vector3) -> void:
	var beach_height := float(_palette_cfg["beach_height"])
	var beach_blend := float(_palette_cfg["beach_blend"])
	var snow_height := float(_palette_cfg["snow_height"])
	var snow_blend := float(_palette_cfg["snow_blend"])
	var rock_slope_low := float(_palette_cfg["rock_slope_low"])
	var rock_slope_high := float(_palette_cfg["rock_slope_high"])
	var moisture_threshold := float(_palette_cfg["moisture_threshold"])
	var moisture_blend := float(_palette_cfg["moisture_blend"])
	var green_max_height := float(_palette_cfg["green_max_height"])

	var data := terrain.data

	for z in size:
		var wz := origin.z + float(z) * _vertex_spacing
		for x in size:
			var wx := origin.x + float(x) * _vertex_spacing
			var h := heights[z * size + x]

			# Central difference on the height grid, clamped at region edges.
			# Seams between regions are acceptable here: neighbouring regions
			# sample the same noise field, so the gradients agree anyway.
			var xl := heights[z * size + maxi(x - 1, 0)]
			var xr := heights[z * size + mini(x + 1, size - 1)]
			var zu := heights[maxi(z - 1, 0) * size + x]
			var zd := heights[mini(z + 1, size - 1) * size + x]
			var run := 2.0 * _vertex_spacing
			var slope := rad_to_deg(atan(Vector2((xr - xl) / run, (zd - zu) / run).length()))

			var base_id := TEX_TAN
			var overlay_id := TEX_TAN
			var blend := 0.0

			if h < beach_height:
				# Shoreline: sand fading up into the plains material.
				base_id = TEX_SAND
				overlay_id = TEX_TAN
				blend = smoothstep(beach_height - beach_blend, beach_height + beach_blend, h)
			else:
				# Moisture decides tan vs green on mid ground; green is capped
				# by altitude so high slopes never look like meadow.
				var moisture := _moisture_noise.get_noise_2d(wx, wz) * 0.5 + 0.5
				var green := (
					smoothstep(
						moisture_threshold - moisture_blend,
						moisture_threshold + moisture_blend,
						moisture
					)
					* (1.0 - smoothstep(green_max_height * 0.6, green_max_height, h))
				)
				base_id = TEX_TAN
				overlay_id = TEX_GRASS
				blend = green

				var snow := smoothstep(snow_height - snow_blend, snow_height, h)
				if snow > 0.0:
					base_id = TEX_ROCK
					overlay_id = TEX_SNOW
					blend = snow

			# Slope wins over everything: anything steep is exposed rock,
			# regardless of altitude or moisture.
			var rocky := smoothstep(rock_slope_low, rock_slope_high, slope)
			if rocky > 0.0:
				if blend > 0.5:
					base_id = overlay_id
				overlay_id = TEX_ROCK
				blend = rocky

			var pos := Vector3(wx, h, wz)
			data.set_control_base_id(pos, base_id)
			data.set_control_overlay_id(pos, overlay_id)
			data.set_control_blend(pos, blend)
