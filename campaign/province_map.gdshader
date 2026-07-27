shader_type canvas_item;

// Colors each pixel by its nearest city (a cheap Voronoi-cell substitute for
// hand-authored province polygons) and darkens pixels near a boundary
// between two cells, so adjacent provinces read as bordered without any
// actual border geometry.

const int MAX_CITIES = 24;

uniform vec2 city_pos[MAX_CITIES];
uniform vec4 city_color[MAX_CITIES];
uniform int city_count = 0;
uniform float map_extent = 2048.0;
uniform float border_width = 10.0;
uniform float border_darken = 0.45;

void fragment() {
	vec2 world_pos = VERTEX + vec2(-map_extent, -map_extent);

	float nearest_dist = 1e20;
	float second_dist = 1e20;
	vec4 nearest_color = vec4(0.0, 0.0, 0.0, 0.0);

	for (int i = 0; i < MAX_CITIES; i++) {
		if (i >= city_count) {
			break;
		}
		float d = distance(world_pos, city_pos[i]);
		if (d < nearest_dist) {
			second_dist = nearest_dist;
			nearest_dist = d;
			nearest_color = city_color[i];
		} else if (d < second_dist) {
			second_dist = d;
		}
	}

	vec4 col = nearest_color;
	if (second_dist - nearest_dist < border_width) {
		col.rgb *= (1.0 - border_darken);
	}
	COLOR = col;
}
