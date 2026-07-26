extends SceneTree

# Dumps the Terrain3D API surface so the builder code targets real names
# instead of names remembered from documentation.
#   godot --headless --script res://tools/introspect.gd


func _dump(cls: String) -> void:
	print("\n===== ", cls, " =====")
	print("-- properties --")
	for p in ClassDB.class_get_property_list(cls, true):
		print("  %s: %s" % [p.name, type_string(p.type)])
	print("-- methods --")
	for m in ClassDB.class_get_method_list(cls, true):
		var args: Array[String] = []
		for a in m.args:
			args.append("%s: %s" % [a.name, type_string(a.type)])
		print("  %s(%s)" % [m.name, ", ".join(args)])


func _initialize() -> void:
	for cls in [
		"Terrain3D",
		"Terrain3DData",
		"Terrain3DAssets",
		"Terrain3DTextureAsset",
		"Terrain3DMaterial",
		"Terrain3DRegion"
	]:
		_dump(cls)
	quit(0)
