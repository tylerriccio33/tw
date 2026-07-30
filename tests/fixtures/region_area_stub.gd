extends Node
## Stand-in for campaign/region_area.gd's set_owner_color(), so
## test_province_map.gd can check apply_ownership()'s color logic without
## building a real Area2D + CollisionPolygon2D scene.

var owner_color_calls: Array[Color] = []


func set_owner_color(color: Color) -> void:
	owner_color_calls.append(color)
