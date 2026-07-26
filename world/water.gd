extends Node3D

## The sea. A single large quad at sea level with a depth-graded shader; it
## extends well past the terrain so the horizon is water rather than the
## bounded edge of the Terrain3D regions.

var errors: PackedStringArray = []

## See terrain_builder.gd for why a completion flag is required in addition to
## the error list.
var built := false

const WATER_SHADER := preload("res://world/water.gdshader")


func build(cfg: Dictionary, sea_level: float, map_extent: float) -> void:
	var material := ShaderMaterial.new()
	material.shader = WATER_SHADER

	material.set_shader_parameter("shallow_color", _color(cfg, "shallow_color"))
	material.set_shader_parameter("deep_color", _color(cfg, "deep_color"))
	material.set_shader_parameter("foam_color", _color(cfg, "foam_color"))
	for key in [
		"depth_falloff",
		"foam_depth",
		"foam_sharpness",
		"wave_scale",
		"wave_speed",
		"wave_strength",
		"wave_time",
		"specular_amount",
		"roughness_amount"
	]:
		if not cfg.has(key):
			errors.append("water.%s missing from world.json" % key)
			return
		material.set_shader_parameter(key, float(cfg[key]))

	var plane := PlaneMesh.new()
	# Well beyond the terrain so the sea reaches the horizon. Subdivided
	# because the shader perturbs normals per-fragment but Godot still needs
	# enough vertices for the depth reconstruction to stay well conditioned.
	var size := map_extent * float(cfg["extent_multiplier"])
	plane.size = Vector2(size, size)
	plane.subdivide_width = 64
	plane.subdivide_depth = 64

	var mesh := MeshInstance3D.new()
	mesh.name = "Water"
	mesh.mesh = plane
	mesh.material_override = material
	mesh.position.y = sea_level
	# The sea is flat and huge; letting it cast shadows gains nothing and
	# costs a full pass over the shadow atlas.
	mesh.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mesh)

	built = true


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("water.%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))
