shader_type spatial;
render_mode cull_disabled, depth_draw_always;

// Campaign-map sea. Deliberately not a physical water shader: at this camera
// distance what matters is a depth-graded colour, a legible shoreline and just
// enough surface movement to not look like painted glass.

uniform vec3 shallow_color : source_color = vec3(0.24, 0.52, 0.62);
uniform vec3 deep_color : source_color = vec3(0.06, 0.17, 0.34);
// World-space depth at which the colour has fully reached deep_color.
uniform float depth_falloff = 130.0;

uniform vec3 foam_color : source_color = vec3(0.92, 0.95, 0.96);
// Water depth below which foam appears - i.e. how far out from the beach.
uniform float foam_depth = 14.0;
uniform float foam_sharpness = 2.4;

uniform float wave_scale = 0.008;
uniform float wave_speed = 0.03;
uniform float wave_strength = 0.05;

// Animation phase. Deliberately NOT the built-in TIME: the screenshot harness
// renders a fixed number of frames and captures, so a TIME-driven surface
// lands on a different phase every run and every diff shows the whole sea as
// changed. Driving the phase from a uniform keeps renders reproducible; a live
// camera can advance this per frame instead.
uniform float wave_time = 0.0;

uniform float specular_amount : hint_range(0.0, 1.0) = 0.45;
uniform float roughness_amount : hint_range(0.0, 1.0) = 0.08;

// Fresnel: how much the sea brightens toward grazing angles vs. the flat
// overhead darkening. fresnel_power shapes the falloff curve.
uniform float fresnel_min = 0.02;
uniform float fresnel_max = 0.55;
uniform float fresnel_power = 4.0;

// Screen-space refraction: how far the seabed image shifts per unit of
// surface normal slope. Scaled down further underwater so shallow, near-flat
// water barely refracts while wave crests visibly bend the seabed behind them.
uniform float refraction_amount = 0.03;

uniform sampler2D depth_texture : hint_depth_texture, filter_linear_mipmap;
uniform sampler2D screen_texture : hint_screen_texture, filter_linear_mipmap;

varying vec3 world_pos;

void vertex() {
	world_pos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}

// Cheap value noise - the sea covers a huge area but is never inspected
// closely, so two octaves of this beat any real gradient noise on cost.
float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float value_noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	f = f * f * (3.0 - 2.0 * f);
	float a = hash(i);
	float b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0));
	float d = hash(i + vec2(1.0, 1.0));
	return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

// Analytic normal of one scrolling value-noise layer, via the central
// difference of the height field. Two of these summed (in the caller) is the
// "two scrolling normal layers" a tiling normal map would give, without
// needing a texture asset - the noise already tiles seamlessly at this scale
// and the sea is never seen up close enough for the lack of real detail to
// register.
vec3 noise_layer_normal(vec2 uv, float scale) {
	float eps = 0.5;
	float h_l = value_noise(uv - vec2(eps, 0.0));
	float h_r = value_noise(uv + vec2(eps, 0.0));
	float h_d = value_noise(uv - vec2(0.0, eps));
	float h_u = value_noise(uv + vec2(0.0, eps));
	return vec3((h_l - h_r) * scale, 2.0 * eps, (h_d - h_u) * scale);
}

void fragment() {
	// Reconstruct how much water sits between this surface and the seabed by
	// comparing the depth buffer against this fragment's own depth. Doing it
	// in view space keeps it correct regardless of camera angle.
	float scene_depth_raw = texture(depth_texture, SCREEN_UV).x;
	vec3 ndc = vec3(SCREEN_UV * 2.0 - 1.0, scene_depth_raw);
	vec4 view_pos = INV_PROJECTION_MATRIX * vec4(ndc, 1.0);
	view_pos.xyz /= view_pos.w;
	float seabed_dist = -view_pos.z;
	float surface_dist = -VERTEX.z;
	float water_depth = max(seabed_dist - surface_dist, 0.0);

	float t = clamp(water_depth / depth_falloff, 0.0, 1.0);
	vec3 color = mix(shallow_color, deep_color, t);

	// Shoreline foam: a band wherever the water is shallow, broken up by the
	// same noise field so it does not read as a clean contour line.
	float shore = 1.0 - clamp(water_depth / foam_depth, 0.0, 1.0);
	float foam_noise = value_noise(world_pos.xz * 0.02 + wave_time * 0.02);
	float foam = pow(shore, foam_sharpness) * (0.65 + 0.35 * foam_noise);

	// Two counter-drifting layers, each contributing its own analytic normal
	// rather than a single diagonal bump, so the surface catches light from
	// more than one direction and does not read as static fuzz.
	vec2 uv1 = world_pos.xz * wave_scale + vec2(wave_time * wave_speed, 0.0);
	vec2 uv2 = world_pos.xz * wave_scale * 1.7 - vec2(0.0, wave_time * wave_speed * 0.8);
	vec3 n1 = noise_layer_normal(uv1, wave_strength);
	vec3 n2 = noise_layer_normal(uv2, wave_strength * 0.6);
	vec3 wave_normal = normalize(n1 + n2 + vec3(0.0, 2.0, 0.0));
	NORMAL = normalize(NORMAL + vec3(wave_normal.x, 0.0, wave_normal.z));

	// Screen-space refraction: shift the seabed sample by the wave normal's
	// tangent-plane slope, scaled up in deeper water where there is more
	// seabed image behind the surface to bend.
	vec2 refract_uv = SCREEN_UV + wave_normal.xz * refraction_amount * mix(0.3, 1.0, t);
	vec3 refracted = textureLod(screen_texture, refract_uv, 0.0).rgb;
	color = mix(refracted, color, clamp(t * 0.85 + 0.15, 0.0, 1.0));
	color = mix(color, foam_color, clamp(foam, 0.0, 1.0));

	// Fresnel: darker looking straight down into the water, brighter at
	// grazing angles where the surface itself is what's reflected.
	float ndotv = clamp(dot(NORMAL, VIEW), 0.0, 1.0);
	float fresnel = fresnel_min + (fresnel_max - fresnel_min) * pow(1.0 - ndotv, fresnel_power);
	color = mix(color, shallow_color * 1.3, fresnel * (1.0 - foam));

	ALBEDO = color;
	ROUGHNESS = roughness_amount;
	SPECULAR = specular_amount + fresnel * 0.3;
	// Water fades to opaque as it deepens; shallows keep a hint of the seabed.
	ALPHA = mix(0.72, 1.0, t);
}
