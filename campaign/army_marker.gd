extends Control
## The piece drawn for one army on the campaign map: a chess-knight icon
## tinted in its owner's color, sitting on a dark backing disc.
##
## Deliberately a different silhouette from CityMarker's keep - a knight reads
## as "unit standing here" at a glance and stays legible even sitting right
## next to (or on top of) a same-colored city plaque, which a plain color
## disc did not: on the owner's own territory the map, the plaque and a flat
## disc are all close to the same hue, so the disc all but vanished into the
## background. The backing disc is drawn in ink regardless of faction color,
## so the shape itself - not just the tint - carries the read, and it's sized
## large enough to stay prominent at typical campaign zoom levels.

const ICON := preload("res://assets/armies/icons/chess_knight.png")
const SIZE := Vector2(56, 68)
const INK := Color(0.10, 0.08, 0.06)
const DISC_RADIUS := 26.0

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


## Where the knight's feet meet the ground - the point the parent should
## centre on the army's world position, mirroring CityMarker.anchor_offset().
func anchor_offset() -> Vector2:
	return Vector2(SIZE.x / 2.0, SIZE.y)


func _draw() -> void:
	var disc_center := Vector2(SIZE.x / 2.0, SIZE.y - DISC_RADIUS * 0.85)
	draw_circle(disc_center, DISC_RADIUS, INK)
	draw_arc(
		disc_center,
		DISC_RADIUS,
		0,
		TAU,
		32,
		Color.WHITE if selected else faction_color,
		3.0 if selected else 2.0
	)

	var icon_size := Vector2(DISC_RADIUS, DISC_RADIUS) * 1.7
	var icon_rect := Rect2(disc_center - icon_size / 2.0, icon_size)
	draw_texture_rect(ICON, icon_rect, false, faction_color)

	if selected:
		draw_arc(disc_center, DISC_RADIUS + 4.0, 0, TAU, 32, Color.WHITE, 2.0)
