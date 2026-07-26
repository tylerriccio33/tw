extends Node3D

## Rivers, roads and faction borders - three different derivations that all end
## up as the same thing: a polyline draped over the terrain and extruded into a
## ribbon. The ribbon builder is shared; only the routing differs.
##
## Rivers run downhill by steepest descent from high ground. Roads connect the
## cities along a minimum spanning tree, wandering by noise so they read as
## routes rather than surveyor's lines. Borders are the boundary of a Voronoi
## partition of the land by nearest city.

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

var _terrain: Node
var _sea_level: float

## Counts of ribbons actually emitted, published for the stats block - not
## the requested/attempted counts, which routinely differ (rivers that never
## reach min_points, roads pruned to nothing after draping).
var river_count := 0
var road_count := 0


func build(
	cfg: Dictionary,
	seed_value: int,
	map_extent: float,
	sea_level: float,
	terrain_builder: Node,
	city_centres: PackedVector3Array,
) -> void:
	_terrain = terrain_builder
	_sea_level = sea_level

	if bool(cfg["rivers_enabled"]):
		_build_rivers(cfg["rivers"], seed_value, map_extent)
	if bool(cfg["roads_enabled"]):
		_build_roads(cfg["roads"], seed_value, city_centres)
	if bool(cfg["borders_enabled"]):
		_build_borders(cfg["borders"], map_extent, city_centres)

	built = true


## River/road count, for the harness's per-render stats block.
func stats() -> Dictionary:
	return {"rivers": river_count, "roads": road_count}


# --- Ribbon construction ----------------------------------------------------


## Extrudes a polyline into a flat ribbon draped on the terrain.
##
## `y_offset` lifts the ribbon clear of the ground; without it the ribbon and
## the terrain z-fight into a stippled mess. Each point is re-seated on the
## terrain rather than interpolated, so the ribbon follows undulations instead
## of cutting through hillsides.
func _make_ribbon(points: PackedVector3Array, width: float, y_offset: float) -> ArrayMesh:
	if points.size() < 2:
		return null

	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)

	var half := width * 0.5
	var left := PackedVector3Array()
	var right := PackedVector3Array()

	for i in points.size():
		# Direction from the neighbours, so corners get a mitre rather than a
		# notch. Endpoints fall back to their single available segment.
		var forward: Vector3
		if i == 0:
			forward = points[1] - points[0]
		elif i == points.size() - 1:
			forward = points[i] - points[i - 1]
		else:
			forward = points[i + 1] - points[i - 1]
		forward.y = 0.0
		if forward.length_squared() < 0.0001:
			forward = Vector3.FORWARD
		forward = forward.normalized()

		var side := Vector3(-forward.z, 0.0, forward.x) * half
		var p := points[i]
		p.y += y_offset
		left.append(p - side)
		right.append(p + side)

	for i in points.size() - 1:
		st.add_vertex(left[i])
		st.add_vertex(left[i + 1])
		st.add_vertex(right[i])

		st.add_vertex(right[i])
		st.add_vertex(left[i + 1])
		st.add_vertex(right[i + 1])

	st.generate_normals()
	return st.commit()


func _add_ribbon(
	parent: Node3D, points: PackedVector3Array, width: float, y_offset: float, material: Material
) -> void:
	var mesh := _make_ribbon(points, width, y_offset)
	if mesh == null:
		return
	var node := MeshInstance3D.new()
	node.mesh = mesh
	node.material_override = material
	# These are flat decals lying on the ground; shadow casting only produces
	# acne along their own edges.
	node.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	parent.add_child(node)


func _flat_material(color: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.85
	mat.specular_mode = BaseMaterial3D.SPECULAR_DISABLED
	return mat


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))


## Moving-average smoothing. Steepest-descent paths are jagged at the sampling
## resolution; a river that has not been smoothed reads as a zigzag.
func _smooth(points: PackedVector3Array, passes: int) -> PackedVector3Array:
	var current := points
	for _p in passes:
		if current.size() < 3:
			return current
		var out := PackedVector3Array()
		out.append(current[0])
		for i in range(1, current.size() - 1):
			out.append((current[i - 1] + current[i] * 2.0 + current[i + 1]) / 4.0)
		out.append(current[current.size() - 1])
		current = out
	return current


## Re-seats a polyline on the terrain, dropping points that fall outside the
## generated regions.
func _drape(points: PackedVector3Array) -> PackedVector3Array:
	var out := PackedVector3Array()
	for p in points:
		var h: float = _terrain.height_at(p.x, p.z)
		if is_finite(h):
			out.append(Vector3(p.x, h, p.z))
	return out


# --- Rivers -----------------------------------------------------------------


func _build_rivers(cfg: Dictionary, seed_value: int, map_extent: float) -> void:
	var parent := Node3D.new()
	parent.name = "Rivers"
	add_child(parent)

	var rng := RandomNumberGenerator.new()
	rng.seed = seed_value + 808

	var material := _flat_material(_color(cfg, "color"))
	var source_height := float(cfg["source_height"])
	var step := float(cfg["step"])
	var max_steps := int(cfg["max_steps"])
	var attempts := int(cfg["source_attempts"])
	var wanted := int(cfg["count"])
	var min_length := int(cfg["min_points"])

	var made := 0
	for _a in attempts:
		if made >= wanted:
			break
		var x := rng.randf_range(-map_extent, map_extent)
		var z := rng.randf_range(-map_extent, map_extent)
		var h: float = _terrain.height_at(x, z)
		if not is_finite(h) or h < source_height:
			continue

		var path := PackedVector3Array()
		var pos := Vector2(x, z)
		for _s in max_steps:
			var here: float = _terrain.height_at(pos.x, pos.y)
			if not is_finite(here):
				break
			path.append(Vector3(pos.x, here, pos.y))
			if here <= _sea_level:
				break

			# Steepest descent: sample the four neighbours and move towards the
			# lowest. A flat or rising neighbourhood means a local basin, and
			# the river simply ends there rather than looping forever.
			var best := here
			var best_dir := Vector2.ZERO
			var neighbours: Array[Vector2] = [Vector2.LEFT, Vector2.RIGHT, Vector2.UP, Vector2.DOWN]
			for dir in neighbours:
				var probe := pos + dir * step
				var ph: float = _terrain.height_at(probe.x, probe.y)
				if is_finite(ph) and ph < best:
					best = ph
					best_dir = dir
			if best_dir == Vector2.ZERO:
				break
			pos += best_dir * step

		if path.size() < min_length:
			continue
		# Widen downstream: a river that is the same width at its mouth as at
		# its source reads as a drawn line, not water.
		path = _drape(_smooth(path, int(cfg["smoothing_passes"])))
		if path.size() < 2:
			continue
		_add_ribbon(parent, path, float(cfg["width"]), float(cfg["y_offset"]), material)
		made += 1

	river_count = made

	if made == 0:
		errors.append(
			(
				(
					"no rivers generated in %d attempts - source_height %.0f may be "
					% [attempts, source_height]
				)
				+ "above the terrain's peak"
			)
		)


# --- Roads ------------------------------------------------------------------


func _build_roads(cfg: Dictionary, seed_value: int, city_centres: PackedVector3Array) -> void:
	if city_centres.size() < 2:
		return

	var parent := Node3D.new()
	parent.name = "Roads"
	add_child(parent)

	var material := _flat_material(_color(cfg, "color"))

	var wander := FastNoiseLite.new()
	wander.seed = seed_value + 909
	wander.noise_type = FastNoiseLite.TYPE_SIMPLEX
	wander.frequency = float(cfg["wander_frequency"])

	var amplitude := float(cfg["wander_amplitude"])
	var samples := int(cfg["samples_per_road"])

	for edge in _minimum_spanning_tree(city_centres):
		var a := city_centres[edge.x]
		var b := city_centres[edge.y]
		var points := _wander_road(a, b, edge.x, wander, amplitude, samples)

		points = _drape(points)
		if points.size() < 2:
			continue
		_add_ribbon(parent, points, float(cfg["width"]), float(cfg["y_offset"]), material)
		road_count += 1


## Straight line from `a` to `b`, perturbed sideways by 1D noise and tapered
## to zero at both ends so the road still arrives at the city it connects.
## Pure and terrain-independent (no draping here) so it is directly testable -
## a bounded-curvature assertion on this is exactly what would have caught
## the historical bug where sampling noise at `t * 100` put ~35 cycles along
## one road, which reads as a zigzag rather than a bend or two.
func _wander_road(
	a: Vector3, b: Vector3, edge_index: int, wander: FastNoiseLite, amplitude: float, samples: int
) -> PackedVector3Array:
	var points := PackedVector3Array()
	var direction := b - a
	direction.y = 0.0
	var perpendicular := Vector3(-direction.z, 0.0, direction.x).normalized()

	for s in samples + 1:
		var t := float(s) / float(samples)
		var p := a.lerp(b, t)
		var taper := sin(t * PI)
		# Sample across ~3 noise units over the whole road, so the route
		# bends once or twice. Advancing far per sample (t * 100) instead
		# puts dozens of noise cycles along one road and it comes out as a
		# zigzag rather than a route. The per-edge offset keeps parallel
		# roads from bending in unison.
		var offset := wander.get_noise_1d(t * 3.0 + float(edge_index) * 37.0)
		points.append(p + perpendicular * offset * amplitude * taper)

	return points


## Prim's algorithm over the cities. An MST gives a connected network with no
## redundant links - every city reachable, nothing criss-crossing.
func _minimum_spanning_tree(points: PackedVector3Array) -> Array[Vector2i]:
	var edges: Array[Vector2i] = []
	var connected := [0]
	var remaining := range(1, points.size())

	while not remaining.is_empty():
		var best_distance := INF
		var best_from := -1
		var best_to := -1
		for i in connected:
			for j in remaining:
				var d := points[i].distance_squared_to(points[j])
				if d < best_distance:
					best_distance = d
					best_from = i
					best_to = j
		if best_to == -1:
			break
		edges.append(Vector2i(best_from, best_to))
		connected.append(best_to)
		remaining.erase(best_to)

	return edges


## Index of the nearest city centre to an XZ point, by squared distance in
## the ground plane. Pure and terrain-independent, factored out of
## _build_borders so the Voronoi partition itself is directly testable.
func _nearest_city(x: float, z: float, city_centres: PackedVector3Array) -> int:
	var best := INF
	var best_index := -1
	for c in city_centres.size():
		var d := Vector2(city_centres[c].x - x, city_centres[c].z - z).length_squared()
		if d < best:
			best = d
			best_index = c
	return best_index


# --- Borders ----------------------------------------------------------------


## Faction borders as the boundary of a nearest-city Voronoi partition,
## sampled on a grid. Where two neighbouring cells belong to different cities
## and both are on land, emit a short ribbon. Sampling on a grid rather than
## computing exact Voronoi edges gives the broken, dashed look the reference
## has, for a fraction of the work.
func _build_borders(cfg: Dictionary, map_extent: float, city_centres: PackedVector3Array) -> void:
	if city_centres.size() < 2:
		return

	var parent := Node3D.new()
	parent.name = "Borders"
	add_child(parent)

	var material := _flat_material(_color(cfg, "color"))
	var cell := float(cfg["cell_size"])
	var width := float(cfg["width"])
	var y_offset := float(cfg["y_offset"])

	var steps := int(map_extent * 2.0 / cell)
	# owner[] is a flat grid of nearest-city indices, -1 for sea.
	var owner := PackedInt32Array()
	owner.resize(steps * steps)

	for iz in steps:
		for ix in steps:
			var x := -map_extent + (float(ix) + 0.5) * cell
			var z := -map_extent + (float(iz) + 0.5) * cell
			var h: float = _terrain.height_at(x, z)
			if not is_finite(h) or h <= _sea_level:
				owner[iz * steps + ix] = -1
				continue
			owner[iz * steps + ix] = _nearest_city(x, z, city_centres)

	# Each boundary is one axis-aligned cell edge, so emitting them individually
	# draws a hard staircase - the single most artificial-looking thing left in
	# the frame. Instead the edges are collected, chained into continuous
	# polylines and run through the same moving-average smoothing the rivers
	# already use, which is what turns the staircase into a drawn line.
	var segments: Array[PackedVector2Array] = []
	for iz in steps:
		for ix in steps:
			var mine := owner[iz * steps + ix]
			if mine < 0:
				continue
			var x := -map_extent + (float(ix) + 0.5) * cell
			var z := -map_extent + (float(iz) + 0.5) * cell

			# Compare against the +X and +Z neighbours only, so each boundary
			# is emitted once rather than twice from both sides.
			if ix + 1 < steps:
				var other := owner[iz * steps + ix + 1]
				if other >= 0 and other != mine:
					(
						segments
						. append(
							PackedVector2Array(
								[
									Vector2(x + cell * 0.5, z - cell * 0.5),
									Vector2(x + cell * 0.5, z + cell * 0.5),
								]
							)
						)
					)
			if iz + 1 < steps:
				var other := owner[(iz + 1) * steps + ix]
				if other >= 0 and other != mine:
					(
						segments
						. append(
							PackedVector2Array(
								[
									Vector2(x - cell * 0.5, z + cell * 0.5),
									Vector2(x + cell * 0.5, z + cell * 0.5),
								]
							)
						)
					)

	var smoothing_passes := int(cfg["smoothing_passes"])
	for chain in _chain_segments(segments):
		if chain.size() < 2:
			continue
		var path := PackedVector3Array()
		for p in chain:
			path.append(Vector3(p.x, 0.0, p.y))
		_add_ribbon(parent, _drape(_smooth(path, smoothing_passes)), width, y_offset, material)


## Walks a soup of unordered grid edges into continuous polylines.
##
## Endpoints are snapped to a key before lookup: they come from the same
## `-map_extent + i * cell` arithmetic on both sides of a shared corner, but
## float addition does not guarantee the two paths produce identical bits, and
## a near-miss would silently break every chain into its original segments.
func _chain_segments(segments: Array[PackedVector2Array]) -> Array[PackedVector2Array]:
	var adjacency: Dictionary = {}
	var points: Dictionary = {}

	for seg in segments:
		var a := _point_key(seg[0])
		var b := _point_key(seg[1])
		points[a] = seg[0]
		points[b] = seg[1]
		if not adjacency.has(a):
			adjacency[a] = ([] as Array[String])
		if not adjacency.has(b):
			adjacency[b] = ([] as Array[String])
		(adjacency[a] as Array[String]).append(b)
		(adjacency[b] as Array[String]).append(a)

	var chains: Array[PackedVector2Array] = []
	var visited: Dictionary = {}

	# Start from endpoints (degree 1) so open runs are walked whole; anything
	# left over afterwards is a closed loop, which can be entered anywhere.
	var starts: Array[String] = []
	for key in adjacency:
		if (adjacency[key] as Array[String]).size() == 1:
			starts.append(key)
	for key in adjacency:
		starts.append(key)

	for start in starts:
		if visited.has(start):
			continue
		var chain := PackedVector2Array()
		var current: String = start
		while current != "" and not visited.has(current):
			visited[current] = true
			chain.append(points[current])
			var next := ""
			for candidate in adjacency[current] as Array[String]:
				if not visited.has(candidate):
					next = candidate
					break
			current = next
		if chain.size() >= 2:
			chains.append(chain)

	return chains


func _point_key(p: Vector2) -> String:
	return "%.2f,%.2f" % [p.x, p.y]
