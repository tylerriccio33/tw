extends Node2D
## Political background of the campaign map: a single full-map ColorRect
## whose shader colors every pixel by its nearest city, giving Voronoi-style
## province cells with a darkened border where two cities are equidistant.
## No polygon geometry, no bitmap asset - update_cities() just pushes new
## city positions/owner colors into the shader each refresh.

const SHADER := preload("res://campaign/province_map.gdshader")
const MAX_CITIES := 40

var _rect: ColorRect
var _material: ShaderMaterial


func setup(map_extent: float) -> void:
	_rect = ColorRect.new()
	_rect.position = Vector2(-map_extent, -map_extent)
	_rect.size = Vector2(map_extent, map_extent) * 2.0
	_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE

	_material = ShaderMaterial.new()
	_material.shader = SHADER
	_material.set_shader_parameter("map_extent", map_extent)
	_rect.material = _material

	add_child(_rect)


func update_cities(positions: PackedVector2Array, colors: PackedColorArray) -> void:
	var count := mini(positions.size(), MAX_CITIES)
	# Shader arrays are fixed-size; pad the unused tail so trailing entries
	# from a previous, larger city count don't leak into this frame - only
	# city_count entries are ever read, but keep the arrays well-formed.
	var padded_pos := PackedVector2Array()
	var padded_color := PackedColorArray()
	padded_pos.resize(MAX_CITIES)
	padded_color.resize(MAX_CITIES)
	for i in count:
		padded_pos[i] = positions[i]
		padded_color[i] = colors[i]

	_material.set_shader_parameter("city_pos", padded_pos)
	_material.set_shader_parameter("city_color", padded_color)
	_material.set_shader_parameter("city_count", count)
