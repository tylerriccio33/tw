extends Control
## A resource deposit on the campaign map: a small faceted gem in the
## resource's own legend color, with the deposit's name underneath.
##
## Like city_marker.gd, the icon is drawn rather than sprited - a resource is a
## gem shape recolored per kind, so a new resource kind is a legend entry, not a
## new asset. These markers are purely visual (the simulation never sees them);
## campaign_ui.gd projects their world positions to screen every time the camera
## moves and writes them here.

const FONT := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const NAME_FONT_SIZE := 10

## The gem itself. Deliberately smaller than a city keep so deposits read as
## secondary landmarks scattered across provinces, not rival settlements.
const GEM_SIZE := Vector2(18, 18)
const NAME_GAP := 2.0

const INK := Color(0.08, 0.06, 0.05)
const BORDER_WIDTH := 1.5
const NAME_COLOR := Color(0.97, 0.96, 0.91)
const NAME_OUTLINE := Color(0.06, 0.05, 0.04)

var resource_name: String = ""
var resource_color: Color = Color.WHITE


func setup(name_text: String, color: Color) -> void:
	resource_name = name_text
	resource_color = color
	custom_minimum_size = Vector2(
		maxf(GEM_SIZE.x, _name_width()), GEM_SIZE.y + NAME_GAP + NAME_FONT_SIZE
	)
	size = custom_minimum_size
	# Deposits are labels on the map, not interactive - let clicks fall through
	# to the province underneath so move orders still work over a resource.
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	queue_redraw()


## Where the gem's centre sits inside this control, so the parent can centre the
## icon on the deposit rather than the whole (name-widened) rect.
func anchor_offset() -> Vector2:
	return Vector2(size.x / 2.0, GEM_SIZE.y / 2.0)


func _draw() -> void:
	var cx := size.x / 2.0
	var gem := Rect2(Vector2(cx - GEM_SIZE.x / 2.0, 0.0), GEM_SIZE)
	_draw_gem(gem)
	_draw_name(cx)


## A cut gem: a hexagonal outline with a lighter top facet, so the marker reads
## as a mineral deposit regardless of the (sometimes pale) resource color.
func _draw_gem(rect: Rect2) -> void:
	var w := rect.size.x
	var h := rect.size.y
	var p := rect.position
	var body := PackedVector2Array(
		[
			p + Vector2(w * 0.25, 0.0),
			p + Vector2(w * 0.75, 0.0),
			p + Vector2(w, h * 0.4),
			p + Vector2(w * 0.5, h),
			p + Vector2(0.0, h * 0.4),
		]
	)
	draw_colored_polygon(body, resource_color)
	# Top facet, a touch lighter for a bit of cut.
	var facet := PackedVector2Array(
		[
			p + Vector2(w * 0.25, 0.0),
			p + Vector2(w * 0.75, 0.0),
			p + Vector2(w * 0.5, h * 0.4),
		]
	)
	draw_colored_polygon(facet, resource_color.lightened(0.35))
	var closed := body.duplicate()
	closed.append(body[0])
	draw_polyline(closed, INK, BORDER_WIDTH)


func _name_width() -> float:
	var font: Font = FONT
	return font.get_string_size(resource_name, HORIZONTAL_ALIGNMENT_LEFT, -1, NAME_FONT_SIZE).x


func _draw_name(cx: float) -> void:
	if resource_name == "":
		return
	var font: Font = FONT
	var baseline := Vector2(cx - _name_width() / 2.0, GEM_SIZE.y + NAME_GAP + NAME_FONT_SIZE * 0.85)
	# Deposits sit over province fills of every color, so the name carries its
	# own dark halo (mirrors city_marker.gd's name treatment).
	for offset in [Vector2(-1, 0), Vector2(1, 0), Vector2(0, -1), Vector2(0, 1)]:
		font.draw_string(
			get_canvas_item(),
			baseline + offset,
			resource_name,
			HORIZONTAL_ALIGNMENT_LEFT,
			-1,
			NAME_FONT_SIZE,
			NAME_OUTLINE
		)
	font.draw_string(
		get_canvas_item(),
		baseline,
		resource_name,
		HORIZONTAL_ALIGNMENT_LEFT,
		-1,
		NAME_FONT_SIZE,
		NAME_COLOR
	)
