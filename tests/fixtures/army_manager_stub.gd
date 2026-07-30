extends Node
## Stand-in for the Rust campaign manager's army-relevant surface, so
## test_army_layer.gd can drive sync()/select()/order_selected_to() without
## the real GDExtension.

signal army_moved(army_id: int, from: Vector2, to: Vector2, spent: float, movement_left: float)
signal army_battle(report: Dictionary)

var armies: Array = []
var faction_id: int = 0
var game_over: bool = false
var move_army_result: bool = true
var garrison_result: int = -1

var move_army_calls: Array = []


func get_state() -> Dictionary:
	return {"armies": armies}


func current_faction_id() -> int:
	return faction_id


func is_game_over() -> bool:
	return game_over


func move_army(army_id: int, x: float, y: float) -> bool:
	move_army_calls.append([army_id, x, y])
	return move_army_result


func garrison_army(_army_id: int) -> int:
	return garrison_result
