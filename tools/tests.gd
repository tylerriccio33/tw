extends SceneTree

## Headless tests for the pure, terrain-independent geometry helpers: MST,
## ribbon extrusion, the Voronoi nearest-city partition, road-wander
## curvature, and height generation. All of this is plain math with no
## rendering or Terrain3D dependency, so it runs in well under a second and
## should have been here from the start instead of these bugs being found by
## eye, renders later.
#   godot --headless --script res://tools/tests.gd

const TerrainBuilder := preload("res://world/terrain_builder.gd")
const Splines := preload("res://world/splines.gd")

var _failures: PackedStringArray = []


func _check(condition: bool, msg: String) -> void:
	if not condition:
		_failures.append(msg)
		printerr("FAIL: ", msg)


func _test_mst() -> void:
	var splines := Splines.new()
	# A 10x10 square. The MST of a 4-cycle is always 3 of the 4 sides
	# (weight 3*10=30) - the two diagonals (~14.14) are strictly longer than
	# any side, so they can never be part of a minimum tree.
	var points := PackedVector3Array(
		[
			Vector3(0, 0, 0),
			Vector3(0, 0, 10),
			Vector3(10, 0, 10),
			Vector3(10, 0, 0),
		]
	)
	var edges: Array[Vector2i] = splines._minimum_spanning_tree(points)

	_check(edges.size() == 3, "MST of 4 points should have 3 edges, got %d" % edges.size())

	var total := 0.0
	var seen := {0: true}
	for e in edges:
		total += points[e.x].distance_to(points[e.y])
		seen[e.x] = true
		seen[e.y] = true
	_check(seen.size() == 4, "MST must connect all 4 points, connected %d" % seen.size())
	_check(
		is_equal_approx(total, 30.0),
		"MST of a 10x10 square should weigh 30 (3 sides), got %.3f" % total
	)

	splines.free()


func _test_ribbon_extrusion() -> void:
	var splines := Splines.new()
	var width := 4.0
	var points := PackedVector3Array(
		[
			Vector3(0, 0, 0),
			Vector3(10, 0, 0),
			Vector3(20, 0, 0),
		]
	)
	var mesh: ArrayMesh = splines._make_ribbon(points, width, 1.0)
	_check(mesh != null, "ribbon over 3 points should produce a mesh")

	var faces := mesh.get_faces()
	_check(
		faces.size() / 3 == 4,
		(
			"a 3-point straight ribbon should be 4 triangles (2 per segment), got %d"
			% (faces.size() / 3)
		)
	)

	# The path runs along +X, so the extrusion should be perpendicular - along
	# Z - by half the width on each side.
	var max_z := -INF
	var min_z := INF
	for v in faces:
		max_z = maxf(max_z, v.z)
		min_z = minf(min_z, v.z)
	_check(
		is_equal_approx(max_z, width * 0.5) and is_equal_approx(min_z, -width * 0.5),
		"ribbon should extend +/-%.1f in Z, got [%.3f, %.3f]" % [width * 0.5, min_z, max_z]
	)

	splines.free()


func _test_voronoi_nearest_city() -> void:
	var splines := Splines.new()
	var cities := PackedVector3Array([Vector3(0, 0, 0), Vector3(100, 0, 0)])

	_check(
		splines._nearest_city(10.0, 0.0, cities) == 0, "point near city 0 should belong to city 0"
	)
	_check(
		splines._nearest_city(90.0, 0.0, cities) == 1, "point near city 1 should belong to city 1"
	)
	_check(
		splines._nearest_city(50.0, 0.0, cities) == 0,
		"equidistant point should resolve to the first city (strict '<' comparison)"
	)

	splines.free()


func _test_road_wander_bounded_curvature() -> void:
	var splines := Splines.new()
	var wander := FastNoiseLite.new()
	wander.seed = 12345
	wander.noise_type = FastNoiseLite.TYPE_SIMPLEX
	wander.frequency = 0.35  # config/world.json network.roads.wander_frequency

	var a := Vector3(0, 0, 0)
	var b := Vector3(1000, 0, 0)
	var samples := 60  # config/world.json network.roads.samples_per_road
	var points := splines._wander_road(a, b, 0, wander, 130.0, samples)

	_check(
		points.size() == samples + 1,
		"wander_road should emit samples+1 points, got %d" % points.size()
	)
	_check(
		points[0].is_equal_approx(a) and points[-1].is_equal_approx(b),
		"wander_road must still arrive exactly at both endpoints (taper -> 0)"
	)

	# Bounded-curvature regression check: the fixed sampling advances only
	# ~3 noise units across the whole road, so the lateral offset should
	# cross zero a handful of times at most. The historical bug (sampling at
	# `t * 100` instead of `t * 3.0`) put ~35 cycles along one road - this
	# would have failed the instant it was introduced instead of being
	# spotted by eye three renders later.
	var sign_changes := 0
	var prev_z := points[1].z  # skip the tapered-to-zero endpoint
	for i in range(2, points.size() - 1):
		var z := points[i].z
		if (z > 0.0) != (prev_z > 0.0) and absf(z) > 0.01 and absf(prev_z) > 0.01:
			sign_changes += 1
		prev_z = z
	_check(
		sign_changes <= 6,
		(
			"road lateral offset crossed zero %d times - expected a handful of " % sign_changes
			+ "bends, not a zigzag (regression check for the t*100 bug)"
		)
	)

	splines.free()


func _test_height_generation() -> void:
	var terrain_cfg: Dictionary = _load_world_json()["terrain"]

	var tb := TerrainBuilder.new()
	tb._cfg = terrain_cfg
	tb._palette_cfg = terrain_cfg["palette"]
	tb._vertex_spacing = float(terrain_cfg["vertex_spacing"])
	tb._half_extent = 1000.0
	tb._build_noise(int(_load_world_json()["seed"]))

	var ocean_depth := float(terrain_cfg["ocean_depth"])
	var size := 8

	# Near the map centre: should read as land, i.e. nowhere near full ocean
	# depth. (Loose bound - the exact value depends on plains/mountain noise,
	# but it must not be the flat seabed value.)
	var centre := tb._generate_heights(size, Vector3(-8.0, 0.0, -8.0))
	for h in centre:
		_check(is_finite(h), "centre heights must be finite, got %s" % h)
	_check(
		centre[0] > -ocean_depth * 0.5,
		(
			"a point near the map centre should read as land, got height %.2f (ocean_depth %.1f)"
			% [centre[0], ocean_depth]
		)
	)

	# Far outside falloff_end + warp_amplitude: land is deterministically 0,
	# so height must be exactly -ocean_depth regardless of noise.
	var far := tb._generate_heights(size, Vector3(3000.0, 0.0, 3000.0))
	for h in far:
		_check(is_finite(h), "far-field heights must be finite, got %s" % h)
		_check(
			is_equal_approx(h, -ocean_depth),
			(
				"far outside the continent falloff, height must equal -ocean_depth exactly "
				+ "(got %.4f, want %.4f)" % [h, -ocean_depth]
			)
		)

	tb.free()


var _world_json_cache: Dictionary


func _load_world_json() -> Dictionary:
	if _world_json_cache.is_empty():
		var text := FileAccess.get_file_as_string("res://config/world.json")
		var json := JSON.new()
		json.parse(text)
		_world_json_cache = json.data
	return _world_json_cache


func _initialize() -> void:
	_test_mst()
	_test_ribbon_extrusion()
	_test_voronoi_nearest_city()
	_test_road_wander_bounded_curvature()
	_test_height_generation()

	if _failures.is_empty():
		print("TESTS OK")
		quit(0)
	else:
		printerr("TESTS FAILED: %d failure(s)" % _failures.size())
		quit(1)
