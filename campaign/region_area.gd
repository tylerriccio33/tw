extends Area2D
## One province polygon on the map. Ported near-verbatim from Thomas
## Holtvedt's grand-strategy-simple tutorial (Region_Area.gd): every child
## Polygon2D starts translucent white and goes fully opaque on hover, so a
## province lights up under the mouse with no per-region art.

signal clicked(region_name: String)

var region_name: String = ""


func _ready() -> void:
	child_entered_tree.connect(_on_child_entered_tree)
	mouse_entered.connect(_on_mouse_entered)
	mouse_exited.connect(_on_mouse_exited)
	input_event.connect(_on_input_event)


func _on_child_entered_tree(node: Node) -> void:
	if node is Polygon2D:
		node.color = Color(1, 1, 1, 0.5)


func _on_mouse_entered() -> void:
	for node in get_children():
		if node is Polygon2D:
			node.color = Color(1, 1, 1, 1)


func _on_mouse_exited() -> void:
	for node in get_children():
		if node is Polygon2D:
			node.color = Color(1, 1, 1, 0.5)


func _on_input_event(_viewport: Node, event: InputEvent, _shape_idx: int) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		clicked.emit(region_name)
