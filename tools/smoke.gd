extends SceneTree

# Smoke test: does the Terrain3D GDExtension actually load and expose the
# code-driven API we designed around? Run with:
#   godot --headless --script res://tools/smoke.gd
# Rendering is unavailable headless, but class registration is not, so this
# isolates "the extension is broken" from "the render is wrong".

func _initialize() -> void:
	var failures: Array[String] = []

	for cls in ["Terrain3D", "Terrain3DData", "Terrain3DAssets", "Terrain3DTextureAsset"]:
		if not ClassDB.class_exists(cls):
			failures.append("class not registered: %s" % cls)

	if failures.is_empty():
		var terrain: Object = ClassDB.instantiate("Terrain3D")
		if terrain == null:
			failures.append("Terrain3D failed to instantiate")
		else:
			print("Terrain3D instantiated: ", terrain.get_class())
			# The methods the whole terrain pipeline depends on.
			for m in ["import_images", "set_height", "add_region_blankp",
					"save_directory", "set_control_base_id", "set_control_blend"]:
				if not ClassDB.class_has_method("Terrain3DData", m):
					failures.append("Terrain3DData missing method: %s" % m)
			terrain.free()

	if failures.is_empty():
		print("SMOKE OK")
		quit(0)
	else:
		for f in failures:
			printerr("SMOKE FAIL: ", f)
		quit(1)
