extends SceneTree

# Why do some vertices end up with an unwritten control word (base 0 / overlay
# 0), showing as black in the control debug view? This reproduces the real
# pipeline on a single region and reports which writes did not land.
#   godot --script res://tools/probe_control.gd

const REGION_SIZE := 1024
const SPACING := 2.0


func _initialize() -> void:
	var terrain := Terrain3D.new()
	root.add_child(terrain)
	terrain.region_size = REGION_SIZE
	terrain.vertex_spacing = SPACING
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://data/probe"))
	terrain.data_directory = "res://data/probe"

	var data := terrain.data

	# A cone: heights sweep the full range so any height-dependent failure
	# shows up as a band, exactly like the render does.
	var heights := PackedFloat32Array()
	heights.resize(REGION_SIZE * REGION_SIZE)
	var centre := float(REGION_SIZE) * 0.5
	for z in REGION_SIZE:
		for x in REGION_SIZE:
			var d := Vector2(float(x) - centre, float(z) - centre).length() / centre
			heights[z * REGION_SIZE + x] = (1.0 - clampf(d, 0.0, 1.0)) * 600.0 - 100.0

	var img := Image.create_from_data(
		REGION_SIZE, REGION_SIZE, false, Image.FORMAT_RF, heights.to_byte_array()
	)
	data.import_images([img, null, null], Vector3.ZERO, 0.0, 1.0)
	print("regions after import: ", data.get_region_count())

	# Write a known, non-zero control everywhere, then read it straight back.
	var attempted := 0
	var failed := 0
	var fail_heights: Array[float] = []
	var step := 4
	for z in range(0, REGION_SIZE, step):
		for x in range(0, REGION_SIZE, step):
			var h := heights[z * REGION_SIZE + x]
			var pos := Vector3(float(x) * SPACING, h, float(z) * SPACING)
			data.set_control_base_id(pos, 3)
			data.set_control_overlay_id(pos, 4)
			data.set_control_blend(pos, 0.5)
			attempted += 1
			if data.get_control_base_id(pos) != 3:
				failed += 1
				if fail_heights.size() < 12:
					fail_heights.append(h)

	print("attempted=%d failed=%d" % [attempted, failed])
	if failed > 0:
		print("sample heights of failed writes: ", fail_heights)
		var lo := fail_heights[0]
		var hi := fail_heights[0]
		for h in fail_heights:
			lo = minf(lo, h)
			hi = maxf(hi, h)
		print("failed-write height span: %.1f .. %.1f" % [lo, hi])

	# Does the Y component matter? If the setters snap to the terrain surface
	# then a deliberately wrong Y should still work.
	var probe := Vector3(512.0, 0.0, 512.0)
	data.set_control_base_id(probe, 2)
	print(
		(
			"write with y=0 (true height %.1f) -> base_id %d"
			% [data.get_height(probe), data.get_control_base_id(probe)]
		)
	)

	print("raw control word at probe: ", data.get_control(probe))
	quit(0)
