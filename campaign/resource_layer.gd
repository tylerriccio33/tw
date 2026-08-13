extends Control
## Screen-space container for the map's resource landmarks.
##
## Resources are a free-point layer (see map_package.layer_points) - purely
## visual, so unlike cities they never touch the Rust state. This layer builds
## one gem marker per landmark once at startup and reprojects them through the
## camera transform every time it moves, exactly like campaign_ui does for
## cities. Kept out of campaign_ui.gd so that glue file stays at its size cap.

const ResourceMarker := preload("res://campaign/resource_marker.gd")

var _markers: Array = []
var _world_positions: Array = []
var _world_to_screen: Callable


## Builds a gem marker per landmark in `package`'s "resources" free-point layer,
## coloring each from that layer's legend. `half_size`/`map_scale` convert the
## authored map-pixel coordinates into the same world space cities use.
func setup(
	package: RefCounted, world_to_screen: Callable, map_scale: float, half_size: Vector2
) -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	_world_to_screen = world_to_screen
	var colors: Dictionary = package.layer_key_colors("resources")
	for point in package.layer_points("resources"):
		var kind := String(point.get("kind", ""))
		var world_pos := Vector2(point["x"], point["y"]) * map_scale - half_size
		var marker := ResourceMarker.new()
		marker.setup(String(point.get("name", kind)), colors.get(kind, Color.WHITE))
		marker.position = _world_to_screen.call(world_pos) - marker.anchor_offset()
		add_child(marker)
		_markers.append(marker)
		_world_positions.append(world_pos)


## Repositions every marker for the current camera transform. Call whenever the
## camera pans/zooms or the window resizes.
func project() -> void:
	for i in _markers.size():
		var marker: Control = _markers[i]
		marker.position = _world_to_screen.call(_world_positions[i]) - marker.anchor_offset()
