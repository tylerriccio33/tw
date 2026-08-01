extends Area2D
## One province's click target on the map. Every child Polygon2D is tinted with
## its owner's color and darkens (and becomes more opaque) on hover, so a
## province visibly reacts to the mouse with no per-province art.
##
## The color is pushed in by province_map.apply_ownership() on every state
## refresh rather than read from a file, because ownership is simulation state -
## a province that changes hands has to change color without anything on disk
## changing.

signal clicked(province_id: int)

const FILL_ALPHA := 0.55
const HOVER_ALPHA := 0.85
## Overrides the owner tint while this province is a valid move target for
## the selected army - see province_map.set_highlighted_provinces.
const HIGHLIGHT_COLOR := Color(0.35, 0.95, 0.45)
const HIGHLIGHT_ALPHA := 0.5
const HIGHLIGHT_HOVER_ALPHA := 0.75
## How far the tint is blended toward black on hover (Color.darkened's
## amount) - this, not the alpha bump alone, is what makes a hovered
## province read as "darkens", not "brightens": raising alpha on its own
## just makes the existing tint more saturated/opaque, which over the
## lighter backdrop art reads as brighter, not darker.
const HOVER_DARKEN := 0.25

## Thick border traced along the province's own boundary while it's a valid
## move target, so the maximum move distance is legible from the outline
## alone rather than only from the (fairly subtle) fill tint. Deliberately
## much heavier than PROVINCE_BORDER_WIDTH in province_map.gd.
const HIGHLIGHT_BORDER_WIDTH := 6.0
const HIGHLIGHT_BORDER_COLOR := Color(0.2, 1.0, 0.35, 0.95)

var province_id: int = -1
var display_name: String = ""

var _owner_color: Color = Color(0.6, 0.6, 0.6)
var _hovered := false
var _highlighted := false
## One Line2D per outer ring, added by province_map when it builds this
## province's polygons (see add_highlight_border). Hidden until highlighted.
var _highlight_borders: Array = []


func _ready() -> void:
	child_entered_tree.connect(_on_child_entered_tree)
	mouse_entered.connect(_on_mouse_entered)
	mouse_exited.connect(_on_mouse_exited)
	input_event.connect(_on_input_event)


func set_owner_color(color: Color) -> void:
	_owner_color = color
	_repaint()


## Registers one of this province's outer-ring polygons as a thick border to
## show while it's highlighted. province_map calls this once per outer ring
## while building the province, right alongside the hairline outline it
## always draws - see province_map._add_provinces.
func add_highlight_border(polygon: PackedVector2Array) -> void:
	var line := Line2D.new()
	line.points = polygon
	line.add_point(polygon[0])
	line.width = HIGHLIGHT_BORDER_WIDTH
	line.default_color = HIGHLIGHT_BORDER_COLOR
	line.joint_mode = Line2D.LINE_JOINT_ROUND
	line.visible = _highlighted
	add_child(line)
	_highlight_borders.append(line)


## Marks whether this province is currently a legal move target - see
## army_layer's move to province_map.set_highlighted_provinces.
func set_highlight(active: bool) -> void:
	if active == _highlighted:
		return
	_highlighted = active
	_repaint()
	for line in _highlight_borders:
		line.visible = _highlighted


func _repaint() -> void:
	var color := _owner_color
	var alpha := FILL_ALPHA
	if _highlighted:
		color = HIGHLIGHT_COLOR
		alpha = HIGHLIGHT_ALPHA
	if _hovered:
		color = color.darkened(HOVER_DARKEN)
		alpha = HIGHLIGHT_HOVER_ALPHA if _highlighted else HOVER_ALPHA
	for node in get_children():
		if node is Polygon2D:
			node.color = Color(color, alpha)


func _on_child_entered_tree(node: Node) -> void:
	if node is Polygon2D:
		_repaint()


func _on_mouse_entered() -> void:
	_hovered = true
	_repaint()


func _on_mouse_exited() -> void:
	_hovered = false
	_repaint()


func _on_input_event(_viewport: Node, event: InputEvent, _shape_idx: int) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		clicked.emit(province_id)
