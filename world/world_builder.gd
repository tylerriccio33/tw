extends Node3D

## Orchestrates the whole render. Everything visible is constructed here from
## config/world.json - there is no authored .tscn content, so an agent can
## change a number, re-render and diff without touching the editor.

const TerrainBuilder := preload("res://world/terrain_builder.gd")
const Lighting := preload("res://world/lighting.gd")
const Water := preload("res://world/water.gd")
const Scatter := preload("res://world/scatter.gd")
const Cities := preload("res://world/cities.gd")
const Splines := preload("res://world/splines.gd")

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

var terrain_builder: TerrainBuilder
var _scatter: Scatter
var _cities: Cities
var _splines: Splines


## Runs `stage.build(...)`, then confirms it actually reached the end. GDScript
## runtime errors unwind only the failing function, so without this check a
## crashed stage looks identical to a successful one and the harness reports
## SHOT OK over a broken render.
func _run_stage(stage_name: String, stage: Object, callable: Callable) -> bool:
	callable.call()
	errors.append_array(stage.get("errors"))
	if not stage.get("built"):
		errors.append(
			(
				"%s did not complete - a runtime error aborted its build(); " % stage_name
				+ "see the SCRIPT ERROR above for the real cause"
			)
		)
		return false
	return true


func build(cfg: Dictionary) -> void:
	for key in ["seed", "terrain", "water", "forests", "cities", "network", "lighting"]:
		if not cfg.has(key):
			errors.append("world.json missing top-level key: %s" % key)
	if not errors.is_empty():
		return

	var lighting := Lighting.new()
	lighting.name = "Lighting"
	add_child(lighting)
	if not _run_stage("lighting", lighting, lighting.build.bind(cfg["lighting"])):
		return

	# The debug view is a terrain-material concern, so it rides along in the
	# terrain config rather than being threaded through as its own argument.
	var terrain_cfg_in: Dictionary = (cfg["terrain"] as Dictionary).duplicate()
	terrain_cfg_in["debug_view"] = (cfg.get("debug", {}) as Dictionary).get("terrain_view", "none")

	terrain_builder = TerrainBuilder.new()
	terrain_builder.name = "TerrainBuilder"
	add_child(terrain_builder)
	if not _run_stage(
		"terrain", terrain_builder, terrain_builder.build.bind(terrain_cfg_in, int(cfg["seed"]))
	):
		return

	var water := Water.new()
	water.name = "Water"
	add_child(water)
	var terrain_cfg: Dictionary = cfg["terrain"]
	if not _run_stage(
		"water",
		water,
		water.build.bind(cfg["water"], float(terrain_cfg["sea_level"]), terrain_builder.map_extent)
	):
		return

	_scatter = Scatter.new()
	_scatter.name = "Forests"
	add_child(_scatter)
	if not _run_stage(
		"forests",
		_scatter,
		_scatter.build.bind(
			cfg["forests"], int(cfg["seed"]), terrain_builder.map_extent, terrain_builder
		)
	):
		return

	_cities = Cities.new()
	_cities.name = "Cities"
	add_child(_cities)
	if not _run_stage(
		"cities",
		_cities,
		_cities.build.bind(
			cfg["cities"], int(cfg["seed"]), terrain_builder.map_extent, terrain_builder
		)
	):
		return

	_splines = Splines.new()
	_splines.name = "Network"
	add_child(_splines)
	if not _run_stage(
		"network",
		_splines,
		_splines.build.bind(
			cfg["network"],
			int(cfg["seed"]),
			terrain_builder.map_extent,
			float(terrain_cfg["sea_level"]),
			terrain_builder,
			_cities.city_centres
		)
	):
		return

	built = true


## Aggregates every stage's stats() plus a whole-scene triangle count, for the
## harness to print once per render. Cheap next to a render (one tree walk)
## and answers "roads are zigzagging" / "cities placed 6 of 6" without
## opening an image.
func stats() -> Dictionary:
	var s := {"triangles": _count_triangles(self)}
	s.merge(terrain_builder.stats())
	s.merge(_scatter.stats())
	s.merge(_cities.stats())
	s.merge(_splines.stats())
	return s


func _count_triangles(node: Node) -> int:
	var total := 0
	if node is MeshInstance3D and node.mesh != null:
		total += (node.mesh as Mesh).get_faces().size() / 3
	elif node is MultiMeshInstance3D and node.multimesh != null:
		var mm := node.multimesh as MultiMesh
		if mm.mesh != null:
			total += int((mm.mesh as Mesh).get_faces().size() / 3) * mm.instance_count
	for child in node.get_children():
		total += _count_triangles(child)
	return total


## Terrain3D cannot discover the capture camera on its own (we render into a
## SubViewport, not the main window), and without it the clipmap never centres
## on the view and _physics_process shuts itself down.
func set_camera(camera: Camera3D) -> void:
	if terrain_builder == null or terrain_builder.terrain == null:
		errors.append("set_camera() called before the terrain was built")
		return
	terrain_builder.terrain.set_camera(camera)


## City centres in world space (x, y=terrain height, z), for callers outside
## this script that need them without reaching into the pseudo-private
## _cities stage node directly.
func city_centres() -> PackedVector3Array:
	return _cities.city_centres
