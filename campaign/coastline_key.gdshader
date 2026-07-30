shader_type canvas_item;

// Cuts both of the backdrop's flat fills to transparent, leaving only the
// drawn coastline stroke. Both colors are read at runtime from the coastline
// layer's own legend, not hardcoded per-map.
//
// The sea fill goes so the animated water layer beneath shows through.
//
// The land fill goes because province fills cover the land now, and anything
// they don't cover is a speck too small for the coastline to have traced.
// Leaving the fill in meant every one of those read as a bright white blot,
// and any province stopping short of the shore was ringed in white.
//
// Land is cut on brightness rather than against the coastline legend's land
// color. The legend describes the color that layer's *raster* uses; the
// backdrop is separate art and draws its fill in its own near-white, which
// is nowhere near it. Brightness is what the fill actually has in common,
// and the drawn stroke is dark, so it survives.
uniform vec4 key_color : source_color = vec4(0.114, 0.208, 0.314, 1.0);
uniform float tolerance = 0.03;
// Set well below the fill's own brightness so the anti-aliased ramp from
// fill to stroke goes with it. Cutting only the pure fill left a pale halo
// ringing every islet too small for the coastline to have traced - the
// fill was gone but its blend into the outline was not.
uniform float land_min_brightness = 0.55;

void fragment() {
	vec4 tex = texture(TEXTURE, UV);
	float brightness = max(tex.r, max(tex.g, tex.b));
	if (distance(tex.rgb, key_color.rgb) < tolerance || brightness >= land_min_brightness) {
		tex.a = 0.0;
	}
	COLOR = tex;
}
