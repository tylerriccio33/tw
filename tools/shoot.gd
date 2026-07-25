extends Node

## Renders every camera preset in config/shots.json to shots/current/*.png,
## then quits. This is the whole application - there is no gameplay yet, so
## what an agent screenshots is exactly what runs.
##
## Capture goes through a SubViewport at full resolution while the OS window
## stays 128x128 (see project.godot), so re-rendering does not take over the
## desktop on every iteration.
##
## Exit code is owned explicitly. Godot exits 0 even after script errors, so
## anything that silently returns 0 here would make the whole harness lie.

const WorldBuilder := preload("res://world/world_builder.gd")

const WORLD_CONFIG := "res://config/world.json"
const SHOTS_CONFIG := "res://config/shots.json"
const OUT_DIR := "res://shots/current"

## Frames to render before capturing. Shadow atlases, sky and the Terrain3D
## clipmap all need a few frames to settle; capturing immediately yields a
## half-lit frame that differs run to run and destroys diff determinism.
const SETTLE_FRAMES := 12

var _errors: PackedStringArray = []


func _ready() -> void:
	_run()


func _fail(msg: String) -> void:
	_errors.append(msg)
	printerr("error: ", msg)


func _load_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		_fail("config not found: %s" % path)
		return {}
	var text := FileAccess.get_file_as_string(path)
	if text.is_empty():
		_fail("config is empty or unreadable: %s (%s)"
			% [path, error_string(FileAccess.get_open_error())])
		return {}
	var json := JSON.new()
	var err := json.parse(text)
	if err != OK:
		_fail("invalid JSON in %s at line %d: %s"
			% [path, json.get_error_line(), json.get_error_message()])
		return {}
	if typeof(json.data) != TYPE_DICTIONARY:
		_fail("%s must contain a JSON object" % path)
		return {}
	return json.data


func _vec3(value: Variant, where: String) -> Vector3:
	if typeof(value) != TYPE_ARRAY or (value as Array).size() != 3:
		_fail("%s must be an [x, y, z] array, got: %s" % [where, value])
		return Vector3.ZERO
	var arr := value as Array
	return Vector3(float(arr[0]), float(arr[1]), float(arr[2]))


func _run() -> void:
	var world_cfg := _load_json(WORLD_CONFIG)
	var shots_cfg := _load_json(SHOTS_CONFIG)
	if not _errors.is_empty():
		return _finish()

	var res_value: Variant = shots_cfg.get("resolution")
	if typeof(res_value) != TYPE_ARRAY or (res_value as Array).size() != 2:
		_fail("shots.json `resolution` must be [width, height]")
		return _finish()
	var res_arr := res_value as Array
	var resolution := Vector2i(int(res_arr[0]), int(res_arr[1]))

	var presets_value: Variant = shots_cfg.get("presets")
	if typeof(presets_value) != TYPE_ARRAY or (presets_value as Array).is_empty():
		_fail("shots.json `presets` must be a non-empty array")
		return _finish()
	var presets := presets_value as Array

	var out_dir := ProjectSettings.globalize_path(OUT_DIR)
	var mk := DirAccess.make_dir_recursive_absolute(out_dir)
	if mk != OK:
		_fail("could not create %s: %s" % [out_dir, error_string(mk)])
		return _finish()

	var viewport := SubViewport.new()
	viewport.size = resolution
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	viewport.msaa_3d = Viewport.MSAA_4X
	viewport.own_world_3d = true
	add_child(viewport)

	var world := WorldBuilder.new()
	world.name = "World"
	viewport.add_child(world)
	world.build(world_cfg)
	for e in world.errors:
		_fail(e)
	if not world.built:
		_fail("world build did not complete; refusing to capture a partial render")
	if not _errors.is_empty():
		return _finish()

	var camera := Camera3D.new()
	camera.name = "ShotCamera"
	camera.near = 1.0
	camera.far = 20000.0
	viewport.add_child(camera)
	camera.make_current()

	world.set_camera(camera)
	for e in world.errors:
		_fail(e)
	if not _errors.is_empty():
		return _finish()

	for i in presets.size():
		var preset: Variant = presets[i]
		if typeof(preset) != TYPE_DICTIONARY:
			_fail("shots.json presets[%d] must be an object" % i)
			continue
		var p := preset as Dictionary
		for key in ["name", "position", "look_at", "fov"]:
			if not p.has(key):
				_fail("shots.json presets[%d] missing key: %s" % [i, key])
		if not _errors.is_empty():
			continue

		var shot_name := String(p["name"])
		var position := _vec3(p["position"], "presets[%d].position" % i)
		var target := _vec3(p["look_at"], "presets[%d].look_at" % i)
		if position.is_equal_approx(target):
			_fail("presets[%d] (%s) position and look_at are identical"
				% [i, shot_name])
			continue

		camera.fov = float(p["fov"])
		camera.global_position = position
		camera.look_at(target, Vector3.UP)

		for _f in SETTLE_FRAMES:
			await RenderingServer.frame_post_draw

		var image := viewport.get_texture().get_image()
		if image == null:
			_fail("viewport produced no image for preset %s" % shot_name)
			continue

		var path := "%s/%s.png" % [out_dir, shot_name]
		var save_err := image.save_png(path)
		if save_err != OK:
			_fail("could not write %s: %s" % [path, error_string(save_err)])
			continue
		print("wrote %s (%dx%d)" % [path, image.get_width(), image.get_height()])

	_finish()


func _finish() -> void:
	if _errors.is_empty():
		print("SHOT OK")
		get_tree().quit(0)
	else:
		printerr("SHOT FAILED with %d error(s)" % _errors.size())
		get_tree().quit(1)
