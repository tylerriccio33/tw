extends Node
## Stand-in for the Rust campaign manager's army-relevant surface, so
## test_army_layer.gd can drive sync()/select()/order_selected_to() without
## the real GDExtension.

signal army_moved(army_id: int, from: Vector2, to: Vector2, spent: float, movement_left: float)
signal army_battle(report: Dictionary)

var armies: Array = []
var cities: Array = []
var faction_id: int = 0
var game_over: bool = false
var move_army_result: bool = true
var garrison_result: int = -1
## army_id -> Array[int] of province ids, read by army_layer's cursor logic
## and province-highlight update. Defaults to empty so a test that never
## touches provinces doesn't have to set this up.
var reachable_by_army: Dictionary = {}

var move_army_calls: Array = []


func get_state() -> Dictionary:
	return {"armies": armies, "cities": cities}


func reachable_provinces(army_id: int) -> Array:
	return reachable_by_army.get(army_id, [])


func current_faction_id() -> int:
	return faction_id


func is_game_over() -> bool:
	return game_over


func move_army(army_id: int, x: float, y: float) -> bool:
	move_army_calls.append([army_id, x, y])
	return move_army_result


func garrison_army(_army_id: int) -> int:
	return garrison_result
