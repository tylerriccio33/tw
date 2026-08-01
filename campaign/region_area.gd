extends Area2D
## One province's click target on the map. Every child Polygon2D is tinted with
## its owner's color and brightens on hover, so a province lights up under the
## mouse with no per-province art.
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

var province_id: int = -1
var display_name: String = ""

var _owner_color: Color = Color(0.6, 0.6, 0.6)
var _hovered := false
var _highlighted := false


func _ready() -> void:
	child_entered_tree.connect(_on_child_entered_tree)
	mouse_entered.connect(_on_mouse_entered)
	mouse_exited.connect(_on_mouse_exited)
	input_event.connect(_on_input_event)


func set_owner_color(color: Color) -> void:
	_owner_color = color
	_repaint()


## Marks whether this province is currently a legal move target - see
## army_layer's move to province_map.set_highlighted_provinces.
func set_highlight(active: bool) -> void:
	if active == _highlighted:
		return
	_highlighted = active
	_repaint()


func _repaint() -> void:
	var color := _owner_color
	var alpha := HOVER_ALPHA if _hovered else FILL_ALPHA
	if _highlighted:
		color = HIGHLIGHT_COLOR
		alpha = HIGHLIGHT_HOVER_ALPHA if _hovered else HIGHLIGHT_ALPHA
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
