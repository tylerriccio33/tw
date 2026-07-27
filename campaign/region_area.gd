extends Area2D
## One province polygon on the map. Ported near-verbatim from Thomas
## Holtvedt's grand-strategy-simple tutorial (Region_Area.gd): every child
## Polygon2D starts translucent white and goes fully opaque on hover, so a
## province lights up under the mouse with no per-region art.

signal clicked(region_name: String)

## Region fills sit above the painted backdrop map, tinted with each
## region's own political color (baked into region_map.png) so ownership
## reads clearly at a glance, HOI4/EU4-style. Hover brightens further.
const FILL_ALPHA := 0.55
const HOVER_ALPHA := 0.85

var region_name: String = ""
## The region's own political color, from region_map.png - set by
## province_map.gd right after this node is created.
var base_color: Color = Color.WHITE


func _ready() -> void:
	child_entered_tree.connect(_on_child_entered_tree)
	mouse_entered.connect(_on_mouse_entered)
	mouse_exited.connect(_on_mouse_exited)
	input_event.connect(_on_input_event)


func _on_child_entered_tree(node: Node) -> void:
	if node is Polygon2D:
		node.color = Color(base_color, FILL_ALPHA)


func _on_mouse_entered() -> void:
	for node in get_children():
		if node is Polygon2D:
			node.color = Color(base_color, HOVER_ALPHA)


func _on_mouse_exited() -> void:
	for node in get_children():
		if node is Polygon2D:
			node.color = Color(base_color, FILL_ALPHA)


func _on_input_event(_viewport: Node, event: InputEvent, _shape_idx: int) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		clicked.emit(region_name)
