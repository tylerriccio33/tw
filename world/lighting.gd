extends Node3D

## Sun, sky and post-processing. Kept separate from terrain because the
## palette in the reference is as much a lighting/tonemap result as it is a
## texture result - these dials get turned a lot.

var errors: PackedStringArray = []

## See terrain_builder.gd - a runtime error aborts build() silently, so the
## caller checks this rather than trusting an empty `errors`.
var built := false

const TONEMAP_MODES := {
	"linear": Environment.TONE_MAPPER_LINEAR,
	"reinhard": Environment.TONE_MAPPER_REINHARDT,
	"filmic": Environment.TONE_MAPPER_FILMIC,
	"aces": Environment.TONE_MAPPER_ACES,
}


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("lighting.%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))


func build(cfg: Dictionary) -> void:
	var sun := DirectionalLight3D.new()
	sun.name = "Sun"
	sun.light_color = _color(cfg, "sun_color")
	sun.light_energy = float(cfg["sun_energy"])
	sun.shadow_enabled = true
	sun.directional_shadow_max_distance = float(cfg["sun_shadow_max_distance"])
	# Four splits: the camera sees both near foreground and distant ridges in
	# the same frame, so a single split visibly bands the shadows.
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS
	sun.directional_shadow_blend_splits = true
	# Terrain at 2-unit vertex spacing under a shadow map covering several
	# kilometres self-shadows badly on steep faces without generous bias.
	# These are high by normal standards; peter-panning is invisible at
	# campaign-map distance, whereas acne reads as black tearing on every ridge.
	sun.shadow_bias = 0.35
	sun.shadow_normal_bias = 14.0
	sun.rotation_degrees = Vector3(
		float(cfg["sun_pitch_degrees"]), float(cfg["sun_yaw_degrees"]), 0.0
	)
	add_child(sun)

	var sky_material := ProceduralSkyMaterial.new()
	sky_material.sky_top_color = _color(cfg, "sky_top_color")
	sky_material.sky_horizon_color = _color(cfg, "sky_horizon_color")
	sky_material.ground_bottom_color = _color(cfg, "sky_horizon_color")
	sky_material.ground_horizon_color = _color(cfg, "sky_horizon_color")
	sky_material.sun_angle_max = 12.0

	var sky := Sky.new()
	sky.sky_material = sky_material

	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = float(cfg["ambient_energy"])
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY

	var tonemap_name := String(cfg["tonemap"])
	if not TONEMAP_MODES.has(tonemap_name):
		errors.append("lighting.tonemap unknown: %s (expected one of %s)"
			% [tonemap_name, TONEMAP_MODES.keys()])
	else:
		env.tonemap_mode = TONEMAP_MODES[tonemap_name]
	env.tonemap_exposure = float(cfg["exposure"])

	# The reference reads as a painted map rather than a photograph: chroma is
	# pushed past neutral and contrast lifted slightly. Straight tonemapped
	# output looks like flat clay by comparison.
	env.adjustment_enabled = true
	env.adjustment_saturation = float(cfg["saturation"])
	env.adjustment_contrast = float(cfg["contrast"])

	if bool(cfg["fog_enabled"]):
		# Depth fog only - aerial perspective on distant ridges is what sells
		# the sense of scale on a campaign map.
		env.fog_enabled = true
		env.fog_mode = Environment.FOG_MODE_DEPTH
		env.fog_light_color = _color(cfg, "fog_color")
		env.fog_density = float(cfg["fog_density"])
		env.fog_sky_affect = 0.0

	var world_env := WorldEnvironment.new()
	world_env.name = "WorldEnvironment"
	world_env.environment = env
	add_child(world_env)

	built = true
