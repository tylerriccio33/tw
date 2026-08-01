extends Node
## Stand-in for campaign/region_area.gd's set_owner_color()/set_highlight(),
## so test_province_map.gd can check apply_ownership()'s color logic and
## set_highlighted_provinces()'s highlight logic without building a real
## Area2D + CollisionPolygon2D scene.

var owner_color_calls: Array[Color] = []
var highlight_calls: Array[bool] = []


func set_owner_color(color: Color) -> void:
	owner_color_calls.append(color)


func set_highlight(active: bool) -> void:
	highlight_calls.append(active)
