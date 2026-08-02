extends Node2D
## Placeholder "swords clash" effect played at a battle's screen location on
## the campaign map, triggered off the Rust `army_battle` signal (see
## campaign_ui.gd::_on_army_battle_ui). No sword sprite exists under assets/
## (only small cursor icons under assets/ui/cursors/), so this draws two
## simple blade shapes with Polygon2D primitives instead of pulling in a new
## asset - per CLAUDE.md, a placeholder Godot-primitive visual is fine here.
##
## Self-contained: instantiate, add as a child, set `position` to the
## battle's screen point, then `await` the `finished` signal - the node frees
## itself once the animation completes.

signal finished

const SLIDE_SECONDS := 0.6
const CLASH_SECONDS := 0.22
const FADE_SECONDS := 0.35
const BLADE_LENGTH := 22.0
const BLADE_WIDTH := 5.0
const START_OFFSET := 42.0

var _left_blade: Polygon2D
var _right_blade: Polygon2D
var _flash: Polygon2D


func _ready() -> void:
	_left_blade = _make_blade(Color(0.85, 0.85, 0.92))
	_right_blade = _make_blade(Color(0.92, 0.78, 0.4))
	_flash = _make_flash()
	add_child(_left_blade)
	add_child(_right_blade)
	add_child(_flash)
	_play()


## A long thin diamond pointing along local +x, standing in for a blade -
## simplest shape that reads as "sword" at HUD scale without an asset.
func _make_blade(color: Color) -> Polygon2D:
	var poly := Polygon2D.new()
	poly.polygon = PackedVector2Array(
		[
			Vector2(-BLADE_LENGTH, 0),
			Vector2(0, -BLADE_WIDTH),
			Vector2(BLADE_LENGTH, 0),
			Vector2(0, BLADE_WIDTH),
		]
	)
	poly.color = color
	return poly


## An 8-point starburst used as the "clang" impact flash, invisible until the
## blades meet.
func _make_flash() -> Polygon2D:
	var poly := Polygon2D.new()
	var pts := PackedVector2Array()
	const SPOKES := 8
	for i in range(SPOKES):
		var a := TAU * float(i) / SPOKES
		var r := 10.0 if i % 2 == 0 else 4.0
		pts.append(Vector2(cos(a), sin(a)) * r)
	poly.polygon = pts
	poly.color = Color(1, 1, 0.85, 0.0)
	return poly


func _play() -> void:
	_left_blade.position = Vector2(-START_OFFSET, -6)
	_left_blade.rotation = deg_to_rad(-30)
	_right_blade.position = Vector2(START_OFFSET, 6)
	_right_blade.rotation = deg_to_rad(150)

	var tween := create_tween()
	tween.set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.set_parallel(true)
	tween.tween_property(_left_blade, "position", Vector2(-4, 0), SLIDE_SECONDS)
	tween.tween_property(_right_blade, "position", Vector2(4, 0), SLIDE_SECONDS)
	tween.chain().set_parallel(true)
	tween.tween_property(_flash, "modulate:a", 1.0, 0.06)
	tween.tween_property(_flash, "scale", Vector2(2.4, 2.4), CLASH_SECONDS)
	tween.chain().set_parallel(false)
	tween.tween_property(self, "modulate:a", 0.0, FADE_SECONDS)
	await tween.finished
	finished.emit()
	queue_free()
