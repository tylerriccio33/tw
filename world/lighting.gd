extends Node3D

## Sun, sky and post-processing. Kept separate from terrain because the
## palette in the reference is as much a lighting/tonemap result as it is a
## texture result - these dials get turned a lot.
##
## Every sub-block (sky, sdfgi, ssao, ssil, volumetric_fog) is optional-free:
## the keys are read strictly, so a missing key is a loud config error rather
## than a silently-skipped effect that costs a render cycle to notice.

var errors: PackedStringArray = []

## See terrain_builder.gd - a runtime error aborts build() silently, so the
## caller checks this rather than trusting an empty `errors`.
var built := false

## AgX is the reason this map stops looking like clay. Filmic clips bright
## snow and sun-glint on water to flat hue-shifted white; AgX rolls highlights
## off while holding their colour, which is what every modern reference render
## (including the target-state screenshot) is graded through.
const TONEMAP_MODES := {
	"linear": Environment.TONE_MAPPER_LINEAR,
	"reinhard": Environment.TONE_MAPPER_REINHARDT,
	"filmic": Environment.TONE_MAPPER_FILMIC,
	"aces": Environment.TONE_MAPPER_ACES,
	"agx": Environment.TONE_MAPPER_AGX,
}

## SDFGI trades cascade count against the world size it can cover. The map
## spans 4096 units, so the default 0.2-unit cells would need far more
## cascades than exist to reach the far coast - hence a config-driven cell
## size measured in whole world units rather than the first-person default.
const SDFGI_Y_SCALES := {
	"50": Environment.SDFGI_Y_SCALE_50_PERCENT,
	"75": Environment.SDFGI_Y_SCALE_75_PERCENT,
	"100": Environment.SDFGI_Y_SCALE_100_PERCENT,
}


func _color(cfg: Dictionary, key: String) -> Color:
	var v: Variant = cfg.get(key)
	if typeof(v) != TYPE_ARRAY or (v as Array).size() != 3:
		errors.append("lighting.%s must be a [r, g, b] array, got: %s" % [key, v])
		return Color.MAGENTA
	var arr := v as Array
	return Color(float(arr[0]), float(arr[1]), float(arr[2]))


func build(cfg: Dictionary) -> void:
	_build_sun(cfg)

	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = _build_sky(cfg["sky"])
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.ambient_light_energy = float(cfg["ambient_energy"])
	env.reflected_light_source = Environment.REFLECTION_SOURCE_SKY

	_apply_tonemap(env, cfg)
	_apply_fog(env, cfg)
	_apply_sdfgi(env, cfg["sdfgi"])
	_apply_ssao(env, cfg["ssao"])
	_apply_ssil(env, cfg["ssil"])
	_apply_volumetric_fog(env, cfg["volumetric_fog"])

	var world_env := WorldEnvironment.new()
	world_env.name = "WorldEnvironment"
	world_env.environment = env
	add_child(world_env)

	built = true


func _build_sun(cfg: Dictionary) -> void:
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
	# Terrain at 1-unit vertex spacing under a shadow map covering several
	# kilometres self-shadows badly on steep faces without generous bias.
	# These are high by normal standards; peter-panning is invisible at
	# campaign-map distance, whereas acne reads as black tearing on every ridge.
	sun.shadow_bias = float(cfg["shadow_bias"])
	sun.shadow_normal_bias = float(cfg["shadow_normal_bias"])
	# The sun is not a point: giving it a real angular diameter (~0.53 degrees
	# as seen from Earth) makes shadow edges soften with distance from the
	# caster, which is most of what separates a rendered shadow from a decal.
	sun.light_angular_distance = float(cfg["sun_angular_distance"])
	sun.rotation_degrees = Vector3(
		float(cfg["sun_pitch_degrees"]), float(cfg["sun_yaw_degrees"]), 0.0
	)
	add_child(sun)


## A physically-based sky rather than a two-colour gradient. Beyond looking
## better on its own, it is what AMBIENT_SOURCE_SKY and REFLECTION_SOURCE_SKY
## sample - so a plausible sky is also what makes shadowed ground pick up a
## believable blue bounce instead of a flat grey wash.
func _build_sky(cfg: Dictionary) -> Sky:
	var sky_material := PhysicalSkyMaterial.new()
	# Rayleigh scattering is the blue; Mie is the forward-scattered haze around
	# the sun that sells atmosphere on a low sun angle.
	sky_material.rayleigh_coefficient = float(cfg["rayleigh_coefficient"])
	sky_material.rayleigh_color = _color(cfg, "rayleigh_color")
	sky_material.mie_coefficient = float(cfg["mie_coefficient"])
	sky_material.mie_eccentricity = float(cfg["mie_eccentricity"])
	sky_material.mie_color = _color(cfg, "mie_color")
	sky_material.turbidity = float(cfg["turbidity"])
	sky_material.sun_disk_scale = float(cfg["sun_disk_scale"])
	sky_material.ground_color = _color(cfg, "ground_color")
	sky_material.energy_multiplier = float(cfg["energy_multiplier"])

	var sky := Sky.new()
	sky.sky_material = sky_material
	# The sky feeds ambient and reflection, so it needs enough resolution to
	# not band across a gradient this wide.
	sky.radiance_size = Sky.RADIANCE_SIZE_256
	return sky


func _apply_tonemap(env: Environment, cfg: Dictionary) -> void:
	var tonemap_name := String(cfg["tonemap"])
	if not TONEMAP_MODES.has(tonemap_name):
		errors.append(
			(
				"lighting.tonemap unknown: %s (expected one of %s)"
				% [tonemap_name, TONEMAP_MODES.keys()]
			)
		)
	else:
		env.tonemap_mode = TONEMAP_MODES[tonemap_name]
	env.tonemap_exposure = float(cfg["exposure"])
	# The luminance mapped to white. AgX wants meaningfully more headroom than
	# the 1.0 default or its highlight rolloff never engages.
	env.tonemap_white = float(cfg["tonemap_white"])

	# The reference reads as a painted map rather than a photograph: chroma is
	# pushed past neutral and contrast lifted slightly. Under AgX this needs a
	# much lighter touch than it did under filmic - AgX desaturates highlights
	# by design, and the old 1.15/1.08 grade was compensating for filmic's
	# flatness rather than expressing an art direction.
	env.adjustment_enabled = true
	env.adjustment_saturation = float(cfg["saturation"])
	env.adjustment_contrast = float(cfg["contrast"])


func _apply_fog(env: Environment, cfg: Dictionary) -> void:
	if not bool(cfg["fog_enabled"]):
		return
	# Depth fog only - aerial perspective on distant ridges is what sells
	# the sense of scale on a campaign map. This is a different job from the
	# volumetric fog below, which does light shafts and valley mist; they are
	# deliberately both on.
	env.fog_enabled = true
	env.fog_mode = Environment.FOG_MODE_DEPTH
	env.fog_light_color = _color(cfg, "fog_color")
	env.fog_density = float(cfg["fog_density"])
	env.fog_sky_affect = 0.0


## Real-time bounce lighting. On a map this size the visible payoff is valley
## floors and the shaded side of ridges picking up colour from the lit ground
## opposite them, instead of falling to a uniform ambient grey.
func _apply_sdfgi(env: Environment, cfg: Dictionary) -> void:
	if not bool(cfg["enabled"]):
		return
	env.sdfgi_enabled = true
	env.sdfgi_use_occlusion = bool(cfg["use_occlusion"])
	env.sdfgi_read_sky_light = bool(cfg["read_sky_light"])
	env.sdfgi_bounce_feedback = float(cfg["bounce_feedback"])
	env.sdfgi_cascades = int(cfg["cascades"])
	env.sdfgi_min_cell_size = float(cfg["min_cell_size"])
	env.sdfgi_energy = float(cfg["energy"])
	env.sdfgi_normal_bias = float(cfg["normal_bias"])
	env.sdfgi_probe_bias = float(cfg["probe_bias"])

	# Terrain is far wider than it is tall, so squashing the cascade volume
	# vertically spends its resolution where the geometry actually is.
	var y_scale := String(cfg["y_scale"])
	if not SDFGI_Y_SCALES.has(y_scale):
		errors.append(
			(
				"lighting.sdfgi.y_scale unknown: %s (expected one of %s)"
				% [y_scale, SDFGI_Y_SCALES.keys()]
			)
		)
	else:
		env.sdfgi_y_scale = SDFGI_Y_SCALES[y_scale]


## Contact shadows where terrain folds meet, and where city walls and tree
## trunks meet the ground. `radius` is in world units and the 1.0 default is
## meaningless at this scale - cliffs need something in the tens.
func _apply_ssao(env: Environment, cfg: Dictionary) -> void:
	if not bool(cfg["enabled"]):
		return
	env.ssao_enabled = true
	env.ssao_radius = float(cfg["radius"])
	env.ssao_intensity = float(cfg["intensity"])
	env.ssao_power = float(cfg["power"])
	env.ssao_detail = float(cfg["detail"])
	env.ssao_horizon = float(cfg["horizon"])
	env.ssao_sharpness = float(cfg["sharpness"])
	env.ssao_light_affect = float(cfg["light_affect"])
	env.ssao_ao_channel_affect = float(cfg["ao_channel_affect"])


## Short-range *coloured* bounce - green light under a canopy, warm tan
## bouncing off the plains onto rock. SDFGI handles the long range; this
## handles what SDFGI's cell size is far too coarse to see.
func _apply_ssil(env: Environment, cfg: Dictionary) -> void:
	if not bool(cfg["enabled"]):
		return
	env.ssil_enabled = true
	env.ssil_radius = float(cfg["radius"])
	env.ssil_intensity = float(cfg["intensity"])
	env.ssil_sharpness = float(cfg["sharpness"])
	env.ssil_normal_rejection = float(cfg["normal_rejection"])


## Atmosphere with actual depth to it: haze pooling in valleys and light
## shafts where ridges break the sun. `length` must reach past the far ridges
## or the fog visibly stops in mid-air partway across the map.
func _apply_volumetric_fog(env: Environment, cfg: Dictionary) -> void:
	if not bool(cfg["enabled"]):
		return
	env.volumetric_fog_enabled = true
	env.volumetric_fog_density = float(cfg["density"])
	env.volumetric_fog_albedo = _color(cfg, "albedo")
	env.volumetric_fog_emission = _color(cfg, "emission")
	env.volumetric_fog_emission_energy = float(cfg["emission_energy"])
	env.volumetric_fog_anisotropy = float(cfg["anisotropy"])
	env.volumetric_fog_length = float(cfg["length"])
	env.volumetric_fog_detail_spread = float(cfg["detail_spread"])
	env.volumetric_fog_gi_inject = float(cfg["gi_inject"])
	env.volumetric_fog_ambient_inject = float(cfg["ambient_inject"])
	env.volumetric_fog_sky_affect = float(cfg["sky_affect"])
	# Temporal reprojection converges the froxel grid over several frames. It
	# is the single largest source of frame-to-frame variance in the render,
	# so it is config-driven: turn it off to trade some noise for a much
	# steadier `make accept`.
	env.volumetric_fog_temporal_reprojection_enabled = bool(cfg["temporal_reprojection"])
	env.volumetric_fog_temporal_reprojection_amount = float(cfg["temporal_reprojection_amount"])
