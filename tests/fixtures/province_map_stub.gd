extends Node2D
## Stands in for campaign/province_map.gd wherever a test only needs its
## `package` field (e.g. campaign_ui.gd's _world_space_province_table reads
## _province_map.package.provinces), without the real Area2D scene setup that
## setup() builds.

var package: RefCounted
