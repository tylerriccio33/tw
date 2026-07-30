shader_type canvas_item;

// Animated stylized water: soft rolling bands between a deep and shallow
// tone, sampled in map-pixel space so scale stays consistent regardless of
// camera zoom.
uniform vec4 deep_color : source_color = vec4(0.114, 0.208, 0.314, 1.0);
uniform vec4 shallow_color : source_color = vec4(0.216, 0.396, 0.431, 1.0);
uniform float wave_scale = 0.015;
uniform float wave_speed = 0.05;

float hash(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	float a = hash(i);
	float b = hash(i + vec2(1.0, 0.0));
	float c = hash(i + vec2(0.0, 1.0));
	float d = hash(i + vec2(1.0, 1.0));
	vec2 u = f * f * (3.0 - 2.0 * f);
	return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}

void fragment() {
	vec2 uv = FRAGCOORD.xy * wave_scale;
	float t = TIME * wave_speed;
	float n1 = noise(uv + vec2(t, -t * 0.5));
	float n2 = noise(uv * 2.3 - vec2(t * 0.7, t * 0.3));
	float waves = clamp(n1 * 0.65 + n2 * 0.35, 0.0, 1.0);
	COLOR = vec4(mix(deep_color.rgb, shallow_color.rgb, waves), 1.0);
}
