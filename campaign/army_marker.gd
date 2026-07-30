extends Control
## The piece drawn for one army on the campaign map: a spear planted in the
## ground with a small pennant in its owner's color.
##
## Deliberately a different silhouette from CityMarker's keep - a spear reads
## as "unit standing here" at a glance and stays legible even sitting right
## next to (or on top of) a same-colored city plaque, which a plain color
## disc did not: on the owner's own territory the map, the plaque and a flat
## disc are all close to the same hue, so the disc all but vanished into the
## background. The pole and pennant border are drawn in ink and white
## respectively regardless of faction color, so the shape itself - not just
## the tint - carries the read.

const SIZE := Vector2(22, 30)
const INK := Color(0.10, 0.08, 0.06)
const POLE_WIDTH := 2.5

var faction_color: Color = Color.WHITE
var selected: bool = false


func setup() -> void:
	custom_minimum_size = SIZE
	size = SIZE
	mouse_filter = Control.MOUSE_FILTER_STOP
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	queue_redraw()


func set_faction_color(color: Color) -> void:
	if color == faction_color:
		return
	faction_color = color
	queue_redraw()


func set_selected(is_selected: bool) -> void:
	if is_selected == selected:
		return
	selected = is_selected
	queue_redraw()


## Where the spear's butt meets the ground - the point the parent should
## centre on the army's world position, mirroring CityMarker.anchor_offset().
func anchor_offset() -> Vector2:
	return Vector2(SIZE.x / 2.0, SIZE.y)


func _draw() -> void:
	var pole_top := Vector2(SIZE.x / 2.0, 2.0)
	var pole_bottom := Vector2(SIZE.x / 2.0, SIZE.y)
	draw_line(pole_top, pole_bottom, INK, POLE_WIDTH)

	var pennant := PackedVector2Array(
		[
			pole_top,
			pole_top + Vector2(SIZE.x / 2.0 - 2.0, SIZE.y * 0.14),
			pole_top + Vector2(0, SIZE.y * 0.30),
		]
	)
	draw_colored_polygon(pennant, faction_color)
	var closed := pennant.duplicate()
	closed.append(pennant[0])
	draw_polyline(closed, Color.WHITE if selected else INK, 2.0 if selected else 1.5)

	if selected:
		draw_arc(pole_bottom - Vector2(0, 3.0), 6.0, 0, TAU, 16, Color.WHITE, 2.0)
