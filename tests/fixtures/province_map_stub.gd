extends Node2D
## Stands in for campaign/province_map.gd wherever a test only needs its
## `package` field (e.g. campaign_ui.gd's _world_space_province_table reads
## _province_map.package.provinces) or its set_highlighted_provinces() call
## (army_layer's reachable-province highlighting), without the real Area2D
## scene setup that setup() builds.

var package: RefCounted

## Each set_highlighted_provinces() call, in order, so a test can assert both
## that it fired and what it was given.
var highlighted_calls: Array = []


func set_highlighted_provinces(ids: Array) -> void:
	highlighted_calls.append(ids)
