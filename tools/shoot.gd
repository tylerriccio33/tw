extends SceneTree
## Renders a scene to a PNG from inside the engine, with no OS involvement.
##
## play_shot.sh drives `screencapture` and System Events, which need
## Accessibility and Screen Recording permissions granted to whichever
## terminal is running them. Where those are missing the shot comes back
## black, or the window can't be resolved at all - and nothing in the
## pipeline notices, because the capture "succeeded".
##
## This reads the viewport's own texture instead, so it works anywhere
## Godot can open a window at all.
##
##   godot -s tools/shoot.gd -- res://campaign/campaign.tscn out.png 1280x800
##
## Frames are given time to settle before the grab: the campaign centres its
## camera in a deferred pass after the window has actually taken its
## requested size, so a shot on frame one catches a stale transform.

const SETTLE_FRAMES := 90


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 2:
		printerr("usage: shoot.gd -- <scene> <out.png> [WIDTHxHEIGHT]")
		quit(1)
		return

	var scene_path: String = args[0]
	var out_path: String = args[1]
	var size := Vector2i(1280, 800)
	if args.size() >= 3:
		var parts: PackedStringArray = String(args[2]).split("x")
		if parts.size() == 2:
			size = Vector2i(int(parts[0]), int(parts[1]))

	DisplayServer.window_set_size(size)
	root.size = size

	var scene: PackedScene = load(scene_path)
	if scene == null:
		printerr("could not load ", scene_path)
		quit(1)
		return
	root.add_child(scene.instantiate())

	for i in SETTLE_FRAMES:
		await process_frame

	var image := root.get_texture().get_image()
	var error := image.save_png(out_path)
	if error != OK:
		printerr("could not write ", out_path, ": ", error)
		quit(1)
		return
	print("SHOT OK ", out_path, " ", image.get_width(), "x", image.get_height())
	quit(0)
