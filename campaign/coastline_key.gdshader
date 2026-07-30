shader_type canvas_item;

// Cuts the backdrop's flat sea fill to transparent so the animated water
// layer beneath it shows through. key_color is read at runtime from the
// coastline layer's own legend, not hardcoded per-map.
uniform vec4 key_color : source_color = vec4(0.114, 0.208, 0.314, 1.0);
uniform float tolerance = 0.03;

void fragment() {
	vec4 tex = texture(TEXTURE, UV);
	if (distance(tex.rgb, key_color.rgb) < tolerance) {
		tex.a = 0.0;
	}
	COLOR = tex;
}
