extends Control
## A city on the campaign map: a keep icon, its owner's crest beside it, and
## the city's name underneath.
##
## The icon is drawn rather than loaded. A settlement marker is a dozen
## rectangles and a couple of triangles, and drawing it means it recolors with
## its owner and stays crisp at any window scale without shipping an atlas of
## per-faction sprites - the crest is the only part that changes hands, and it
## is a filled rect.
##
## Placement is the parent's job. campaign_ui.gd projects world positions to
## screen every time the camera moves and writes them here; this node only
## knows how to draw itself and to report a click.

signal clicked

const FONT := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const NAME_FONT_SIZE := 14

## The clickable plaque holding the keep. Everything else is drawn relative
## to it, and the control itself is wider to leave room for the crest.
## Sized to hold its own next to ArmyMarker's ~52px-diameter disc
## (army_marker.gd) rather than reading as a minor dot beside it.
const KEEP_SIZE := Vector2(44, 44)
const CREST_SIZE := Vector2(20, 25)
const CREST_GAP := 5.0
const NAME_GAP := 4.0

const PLAQUE_COLOR := Color(0.93, 0.91, 0.84)
const INK := Color(0.10, 0.08, 0.06)
const BORDER_WIDTH := 2.5
const NAME_COLOR := Color(0.97, 0.96, 0.91)
const NAME_OUTLINE := Color(0.06, 0.05, 0.04)

var city_name: String = ""
var faction_color: Color = Color.WHITE


func setup(name_text: String) -> void:
	city_name = name_text
	custom_minimum_size = Vector2(
		KEEP_SIZE.x + CREST_GAP + CREST_SIZE.x, KEEP_SIZE.y + NAME_GAP + NAME_FONT_SIZE
	)
	size = custom_minimum_size
	mouse_filter = Control.MOUSE_FILTER_STOP
	mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	queue_redraw()


func set_faction_color(color: Color) -> void:
	if color == faction_color:
		return
	faction_color = color
	queue_redraw()


## Where the marker's anchor point sits inside its own rect - the foot of the
## keep, so the parent can centre the icon on the city rather than centring
## the whole control (which would drag it off by half the name and crest).
func anchor_offset() -> Vector2:
	return Vector2(KEEP_SIZE.x / 2.0, KEEP_SIZE.y / 2.0)


func _draw() -> void:
	_draw_keep(Rect2(Vector2.ZERO, KEEP_SIZE))
	_draw_crest(
		Rect2(Vector2(KEEP_SIZE.x + CREST_GAP, (KEEP_SIZE.y - CREST_SIZE.y) / 2.0), CREST_SIZE)
	)
	_draw_name()


## A walled keep seen head on: a curtain wall with a crenellated parapet, a
## gate, and a taller tower behind it.
func _draw_keep(rect: Rect2) -> void:
	draw_rect(rect, PLAQUE_COLOR, true)
	draw_rect(rect, INK, false, BORDER_WIDTH)

	var unit := rect.size / 12.0
	var wall_top := rect.position.y + unit.y * 6.0
	var wall_bottom := rect.position.y + unit.y * 10.0

	# Central tower, standing a couple of units proud of the wall.
	draw_rect(
		Rect2(
			rect.position + Vector2(unit.x * 5.0, unit.y * 2.5),
			Vector2(unit.x * 2.0, unit.y * 7.5),
		),
		INK,
		true
	)
	# Curtain wall.
	draw_rect(
		Rect2(
			Vector2(rect.position.x + unit.x * 2.0, wall_top),
			Vector2(unit.x * 8.0, wall_bottom - wall_top),
		),
		INK,
		true
	)
	# Merlons along the parapet, and one pair capping the tower.
	for i in 4:
		draw_rect(
			Rect2(
				Vector2(rect.position.x + unit.x * (2.0 + i * 2.0), wall_top - unit.y * 1.2),
				Vector2(unit.x * 1.2, unit.y * 1.2),
			),
			INK,
			true
		)
	# Gate, punched back out of the wall in the plaque's own color.
	draw_rect(
		Rect2(
			Vector2(rect.position.x + unit.x * 5.25, wall_bottom - unit.y * 2.4),
			Vector2(unit.x * 1.5, unit.y * 2.4),
		),
		PLAQUE_COLOR,
		true
	)


## The owner's colors on a hanging banner, so who holds a city reads at a
## glance without a faction name written across the map.
func _draw_crest(rect: Rect2) -> void:
	var tail := rect.size.y * 0.28
	var banner := PackedVector2Array(
		[
			rect.position,
			rect.position + Vector2(rect.size.x, 0),
			rect.position + Vector2(rect.size.x, rect.size.y - tail),
			rect.position + Vector2(rect.size.x / 2.0, rect.size.y),
			rect.position + Vector2(0, rect.size.y - tail),
		]
	)
	draw_colored_polygon(banner, faction_color)
	var closed := banner.duplicate()
	closed.append(banner[0])
	draw_polyline(closed, INK, BORDER_WIDTH)


func _draw_name() -> void:
	if city_name == "":
		return
	var font: Font = FONT
	var width := font.get_string_size(city_name, HORIZONTAL_ALIGNMENT_LEFT, -1, NAME_FONT_SIZE).x
	var baseline := Vector2(
		(KEEP_SIZE.x - width) / 2.0, KEEP_SIZE.y + NAME_GAP + NAME_FONT_SIZE * 0.85
	)
	# Cities sit over province fills of every color, so the name carries its
	# own dark halo instead of relying on contrast with what's underneath.
	for offset in [Vector2(-1, 0), Vector2(1, 0), Vector2(0, -1), Vector2(0, 1)]:
		font.draw_string(
			get_canvas_item(),
			baseline + offset,
			city_name,
			HORIZONTAL_ALIGNMENT_LEFT,
			-1,
			NAME_FONT_SIZE,
			NAME_OUTLINE
		)
	font.draw_string(
		get_canvas_item(),
		baseline,
		city_name,
		HORIZONTAL_ALIGNMENT_LEFT,
		-1,
		NAME_FONT_SIZE,
		NAME_COLOR
	)


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		clicked.emit()
