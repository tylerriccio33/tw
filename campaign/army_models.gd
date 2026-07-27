extends Node3D
## Standing-army models: one Quaternius CC0 knight per living army, planted on
## the terrain under the 2D disc marker that army_layer.gd still owns for
## clicks/selection/tooltips. This node only draws; orders and selection stay
## in army_layer.gd.
##
## Model comes from Quaternius's "LowPoly Animated Knight" (CC0, vendored by
## `make armies` - see tools/fetch_armies.py into assets/armies/kit/). Each
## army gets its own instance so it can be tinted solid faction color, the
## same read the disc markers already use.

const KIT_DIR := "res://assets/armies/kit/"
const MODEL_PATH := KIT_DIR + "KnightCharacter.fbx"

## The source model is real-world (metre) scale; armies need to read at
## campaign-map zoom the way a ~3-unit-tall building does, so it's blown up
## well past human height rather than left true to scale.
const MODEL_SCALE := 10.0

var _manager: Node
var _terrain: Node
var _faction_colors: Array[Color] = []

var _template: PackedScene
var _load_error := ""

## army id -> Node3D instance
var _instances: Dictionary = {}


func setup(manager: Node, terrain: Node, faction_colors: Array[Color]) -> void:
	_manager = manager
	_terrain = terrain
	_faction_colors = faction_colors

	if not ResourceLoader.exists(MODEL_PATH):
		_load_error = "army_models: missing %s - run `make armies` to vendor the kit" % MODEL_PATH
		printerr(_load_error)
		return
	_template = load(MODEL_PATH)


func _ground_pos(ground: Vector2) -> Vector3:
	var height := 0.0
	if _terrain != null:
		var sampled: float = _terrain.height_at(ground.x, ground.y)
		if is_finite(sampled):
			height = sampled
	return Vector3(ground.x, height, ground.y)


func _ensure_instance(army: Dictionary) -> Node3D:
	var id: int = int(army["id"])
	if _instances.has(id):
		return _instances[id]

	var instance := _template.instantiate() as Node3D
	instance.scale = Vector3.ONE * MODEL_SCALE
	add_child(instance)
	_tint(instance, _faction_colors[int(army["owner"]) % _faction_colors.size()])
	_instances[id] = instance
	return instance


## Flat-colors every mesh surface in the model so an army reads by faction
## color at a glance, matching the disc markers army_layer.gd draws.
func _tint(node: Node, color: Color) -> void:
	if node is MeshInstance3D:
		var mesh_instance := node as MeshInstance3D
		var mat := StandardMaterial3D.new()
		mat.albedo_color = color
		mat.roughness = 0.6
		for surf in mesh_instance.mesh.get_surface_count() if mesh_instance.mesh != null else 0:
			mesh_instance.set_surface_override_material(surf, mat)
	for child in node.get_children():
		_tint(child, color)


## Rebuilds instances from a state snapshot: adds one per living army, moves
## the rest to their (possibly mid-move) position, and drops the ones whose
## army died.
func sync(state: Dictionary, ground_positions: Dictionary) -> void:
	if _template == null:
		return

	var live: Dictionary = {}
	for army in state.get("armies", []):
		var id: int = int(army["id"])
		live[id] = true
		var instance := _ensure_instance(army)
		var ground: Vector2 = ground_positions.get(id, Vector2(float(army["x"]), float(army["y"])))
		instance.position = _ground_pos(ground)

	for id: int in _instances.keys():
		if not live.has(id):
			var instance: Node3D = _instances[id]
			instance.queue_free()
			_instances.erase(id)
