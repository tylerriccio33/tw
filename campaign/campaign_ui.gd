extends Node3D
## Thin GDScript glue: builds the real 3D world from config/world.json, sets
## up a tilted strategy-map camera over it (WASD/edge pan, scroll zoom) and
## drives the Rust CampaignManager with the world's real city positions
## instead of a hardcoded square. City markers/dropdown/end-turn button
## render state on every signal instead of polling - unchanged from before
## this script also owned world/camera setup.

const WorldBuilder := preload("res://world/world_builder.gd")
const WORLD_CONFIG := "res://config/world.json"
const MAX_TURNS := 10

# Camera pan/zoom tuning, matching target-state.png's close-in TW campaign
# framing (terrain/water fills the frame, no sky band) rather than a distant
# island overview. WASD/edge-pan slides the ground-plane focus point the
# camera looks at; the scroll wheel scales CAM_HEIGHT/CAM_BACK from that
# focus. Height/back are absolute world units (not scaled by map_extent) so
# zoom stays a local, ground-level view regardless of map size.
const CAM_HEIGHT := 750.0
const CAM_BACK := 900.0
const PAN_SPEED := 0.6  # fraction of map_extent per second, at zoom 1.0
const EDGE_PAN_MARGIN := 24.0  # px from viewport edge that triggers edge-pan
const ZOOM_STEP := 0.1  # per scroll-wheel notch
const ZOOM_KEY_SPEED := 1.2  # zoom units/sec while Z/X is held
const MIN_ZOOM := 0.6
const MAX_ZOOM := 2.2

const FACTION_COLORS: Array[Color] = [
	Color.INDIAN_RED, Color.CORNFLOWER_BLUE, Color.MEDIUM_SEA_GREEN, Color.GOLDENROD
]

# Total War-style HUD palette, sampled off the reference reveal-stream
# screenshot: a mid steel-blue header/tab color, cream parchment panels, and
# a maroon building tray - kept as named constants since they're reused
# across the city panel, tabs, and building tray.
const HUD_BLUE := Color(0.247, 0.353, 0.51)
const HUD_BLUE_DARK := Color(0.11, 0.15, 0.22)
const HUD_CREAM := Color(0.95, 0.94, 0.88)
const HUD_MAROON := Color(0.55, 0.1, 0.08)

const FONT_MEDIUM := preload("res://assets/fonts/Baloo2-Medium.ttf")
const FONT_SEMIBOLD := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const FONT_BOLD := preload("res://assets/fonts/Baloo2-Bold.ttf")

@onready var manager: Node = $CampaignManager
@onready var status_label: Label = $UI/StatusLabel
@onready var log_label: RichTextLabel = $UI/LogLabel
@onready var target_option: OptionButton = $UI/Controls/TargetOption
@onready var attack_button: Button = $UI/Controls/AttackButton
@onready var bottom_banner: Control = $UI/BottomBanner
@onready var cities_root: Control = $CityMarkers

var end_turn_button: Button

var city_markers: Dictionary = {}

var _world_camera: Camera3D
var _city_centres: PackedVector3Array = []
var _marker_positions: Dictionary = {}
var _map_extent: float = 0.0
var _cam_focus := Vector3.ZERO
var _cam_zoom := 1.0
var _selected_city_id: int = -1

# Bottom-banner widgets that get new data every _refresh(); built once in
# _build_bottom_banner() and then just written into on each turn/battle event.
var _city_panel_name_label: Label
var _city_panel_owner_tab: ColorRect
var _city_panel_perk_label: Label
var _city_stat_value_labels: Dictionary = {}

const STAT_ROWS := [
	# [state key, display label, icon glyph, per-income multiplier]
	["nobles", "Nobles", "♛", 6],
	["townsfolk", "Townsfolk", "☺", 180],
	["peasants", "Peasants", "⚘", 2000],
	["food", "Food", "☘", 200],
	["region_wealth", "Region wea...", "◉", 100],
	["income", "Income", "$", 5],
]

const BUILDINGS := [
	{"name": "Farmland", "level": 1},
	{"name": "Lumbercamp", "level": 1},
	{"name": "Mine", "level": 0},
	{"name": "Quarry", "level": 1},
]
const LOCKED_BUILDINGS := ["Castle", "Castle", "City"]


func _load_world_config() -> Dictionary:
	if not FileAccess.file_exists(WORLD_CONFIG):
		return {}
	var text := FileAccess.get_file_as_string(WORLD_CONFIG)
	if text.is_empty():
		return {}
	var json := JSON.new()
	if json.parse(text) != OK or typeof(json.data) != TYPE_DICTIONARY:
		return {}
	return json.data


func _fail_to_start(message: String) -> void:
	printerr("error: ", message)
	status_label.text = message
	attack_button.disabled = true
	end_turn_button.disabled = true


func _ready() -> void:
	# project.godot pins the main window to 128x128 for the offscreen
	# screenshot harness, so nothing there sets up UI scaling. Configure it
	# here instead, scoped to the live campaign session only: canvas_items
	# scaling rescales every Control (fonts, buttons, banner cards) by the
	# ratio of the actual window size to this 1280x800 baseline - the
	# resolution the HUD's pixel offsets/card sizes were laid out against -
	# so the HUD grows instead of staying pinned to its original pixel size
	# when the window is resized.
	get_window().content_scale_mode = Window.CONTENT_SCALE_MODE_CANVAS_ITEMS
	get_window().content_scale_size = Vector2i(1280, 800)
	get_window().content_scale_aspect = Window.CONTENT_SCALE_ASPECT_EXPAND

	_build_bottom_banner()

	var cfg := _load_world_config()
	if cfg.is_empty():
		_fail_to_start("could not load %s" % WORLD_CONFIG)
		return

	var world := WorldBuilder.new()
	world.name = "World"
	add_child(world)
	world.build(cfg)
	if not world.built or not world.errors.is_empty():
		_fail_to_start("world build failed: %s" % ", ".join(world.errors))
		return

	_world_camera = Camera3D.new()
	_world_camera.name = "WorldCamera"
	_world_camera.projection = Camera3D.PROJECTION_PERSPECTIVE
	_world_camera.fov = 45.0
	_world_camera.near = 1.0
	_world_camera.far = 20000.0
	_map_extent = world.terrain_builder.map_extent
	add_child(_world_camera)

	world.set_camera(_world_camera)
	if not world.errors.is_empty():
		_fail_to_start("set_camera failed: %s" % ", ".join(world.errors))
		return

	_city_centres = world.city_centres()
	if _city_centres.is_empty():
		_fail_to_start("world built with 0 cities - nothing to play")
		return

	var positions := PackedVector2Array()
	for c in _city_centres:
		positions.append(Vector2(c.x, c.z))

	# Close-in TW-style campaign framing (matches target-state.png): start
	# centred over the cities' midpoint rather than the map origin, which can
	# land anywhere from open ocean to a bare cliff depending on the seed.
	var centre := Vector3.ZERO
	for c in _city_centres:
		centre += c
	centre /= _city_centres.size()
	_cam_focus = Vector3(centre.x, 0.0, centre.z)
	_cam_zoom = 1.0
	_update_camera_transform()
	_world_camera.make_current()

	manager.turn_started.connect(_on_turn_started)
	manager.battle_resolved.connect(_on_battle_resolved)
	manager.game_over.connect(_on_game_over)
	attack_button.pressed.connect(_on_attack_pressed)

	# start_game_from_positions() emits turn_started synchronously, so
	# _refresh() can run before this function returns - city markers must
	# already exist by then. _ensure_city_marker() below makes marker
	# creation lazy so there is no ordering requirement between the two.
	manager.start_game_from_positions(positions, MAX_TURNS)
	_project_markers()
	_refresh()
	get_viewport().size_changed.connect(_project_markers)


## Recomputes the camera's world transform from _cam_focus/_cam_zoom, keeping
## the fixed TW-style pitch. Called whenever pan or zoom actually changes the
## camera, not every frame - the transform is otherwise unchanged.
func _update_camera_transform() -> void:
	_world_camera.global_position = _cam_focus + Vector3(0.0, CAM_HEIGHT, CAM_BACK) * _cam_zoom
	_world_camera.look_at(_cam_focus, Vector3.UP)


func _process(delta: float) -> void:
	if _world_camera == null:
		return
	var move := Vector2.ZERO
	if Input.is_key_pressed(KEY_W):
		move.y -= 1.0
	if Input.is_key_pressed(KEY_S):
		move.y += 1.0
	if Input.is_key_pressed(KEY_A):
		move.x -= 1.0
	if Input.is_key_pressed(KEY_D):
		move.x += 1.0

	# Edge-pan: cursor within EDGE_PAN_MARGIN px of a viewport edge pans same
	# as the matching WASD key, so the mouse alone can scroll the map.
	var viewport := get_viewport()
	if viewport.get_window().has_focus():
		var mouse_pos := viewport.get_mouse_position()
		var size := viewport.get_visible_rect().size
		if mouse_pos.x <= EDGE_PAN_MARGIN:
			move.x -= 1.0
		elif mouse_pos.x >= size.x - EDGE_PAN_MARGIN:
			move.x += 1.0
		if mouse_pos.y <= EDGE_PAN_MARGIN:
			move.y -= 1.0
		elif mouse_pos.y >= size.y - EDGE_PAN_MARGIN:
			move.y += 1.0

	var changed := false

	if move != Vector2.ZERO:
		move = move.normalized()
		var forward := -_world_camera.global_transform.basis.z
		forward.y = 0.0
		forward = forward.normalized()
		var right := _world_camera.global_transform.basis.x
		right.y = 0.0
		right = right.normalized()
		var offset := (
			(right * move.x + forward * -move.y) * PAN_SPEED * _map_extent * _cam_zoom * delta
		)
		_cam_focus = (_cam_focus + offset).limit_length(_map_extent)
		changed = true

	# Z zooms in, X zooms out - mirrors the scroll wheel but held-key smooth.
	var zoom_delta := 0.0
	if Input.is_key_pressed(KEY_Z):
		zoom_delta -= 1.0
	if Input.is_key_pressed(KEY_X):
		zoom_delta += 1.0
	if zoom_delta != 0.0:
		_cam_zoom = clampf(_cam_zoom + zoom_delta * ZOOM_KEY_SPEED * delta, MIN_ZOOM, MAX_ZOOM)
		changed = true

	if changed:
		_update_camera_transform()
		_project_markers()


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP:
			_cam_zoom = clampf(_cam_zoom - ZOOM_STEP, MIN_ZOOM, MAX_ZOOM)
			_update_camera_transform()
			_project_markers()
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
			_cam_zoom = clampf(_cam_zoom + ZOOM_STEP, MIN_ZOOM, MAX_ZOOM)
			_update_camera_transform()
			_project_markers()


## Screen position of every city, from its real 3D centre through the
## current camera transform. Re-run whenever the camera pans/zooms (see
## _process/_unhandled_input above) as well as once at startup and on resize.
func _project_markers() -> void:
	for i in _city_centres.size():
		var pos := _world_camera.unproject_position(_city_centres[i])
		_marker_positions[i] = pos
		if city_markers.has(i):
			var marker: ColorRect = city_markers[i]
			marker.position = pos - marker.size / 2.0


func _ensure_city_marker(city: Dictionary) -> ColorRect:
	var id: int = int(city["id"])
	if city_markers.has(id):
		return city_markers[id]

	var pos: Vector2 = _marker_positions.get(id, Vector2.ZERO)

	var marker := ColorRect.new()
	marker.size = Vector2(32, 32)
	marker.position = pos - marker.size / 2.0
	marker.mouse_filter = Control.MOUSE_FILTER_STOP
	marker.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	marker.gui_input.connect(_on_city_marker_input.bind(id))
	cities_root.add_child(marker)
	city_markers[id] = marker

	var label := Label.new()
	label.text = city["name"]
	label.position = pos + Vector2(-16, 18)
	cities_root.add_child(label)

	return marker


## Clicking a city marker selects it: if it belongs to another faction it's
## picked as the attack target (mirrored into the dropdown attack_city()
## already reads from), otherwise it's just shown in the bottom info panel.
func _on_city_marker_input(event: InputEvent, city_id: int) -> void:
	if not (
		event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT
	):
		return
	_selected_city_id = city_id
	for i in target_option.item_count:
		if target_option.get_item_id(i) == city_id:
			target_option.select(i)
			break
	_refresh_bottom_banner(manager.get_state())


func _refresh() -> void:
	var state: Dictionary = manager.get_state()

	for city in state["cities"]:
		var marker: ColorRect = _ensure_city_marker(city)
		marker.color = FACTION_COLORS[int(city["owner"]) % FACTION_COLORS.size()]

	var lines: Array[String] = []
	lines.append("Turn %d / %d" % [state["turn"], state["max_turns"]])
	for faction in state["factions"]:
		var alive_marker := "" if faction["alive"] else " (eliminated)"
		lines.append(
			(
				"%s: $%d, %d cities%s"
				% [faction["name"], faction["money"], faction["cities"], alive_marker]
			)
		)
	status_label.text = "\n".join(lines)

	_rebuild_target_options(state)
	_refresh_bottom_banner(state)

	if state["game_over"]:
		attack_button.disabled = true
		end_turn_button.disabled = true
		var winner_id: int = state["winner"]
		var winner_name := "nobody"
		for faction in state["factions"]:
			if int(faction["id"]) == winner_id:
				winner_name = faction["name"]
		_append_log("[b]Game over. %s wins.[/b]" % winner_name)


func _rebuild_target_options(state: Dictionary) -> void:
	target_option.clear()
	var current_faction: int = state["current_faction"]
	for city in state["cities"]:
		if int(city["owner"]) != current_faction:
			target_option.add_item(
				"%s (owned by faction %d)" % [city["name"], city["owner"]], int(city["id"])
			)
	attack_button.disabled = target_option.item_count == 0 or state["game_over"]


func _on_attack_pressed() -> void:
	if target_option.item_count == 0:
		return
	var target_city_id: int = target_option.get_item_id(target_option.selected)
	manager.attack_city(target_city_id)


func _on_end_turn_pressed() -> void:
	manager.end_turn()


func _on_turn_started(faction_id: int, turn: int) -> void:
	_append_log("Turn %d: faction %d begins." % [turn, faction_id])
	_refresh()


func _on_battle_resolved(
	attacker_id: int, defender_id: int, city_id: int, attacker_won: bool, defender_eliminated: bool
) -> void:
	var verb := "captured" if attacker_won else "failed to capture"
	var msg := "Faction %d %s city %d from faction %d." % [attacker_id, verb, city_id, defender_id]
	if defender_eliminated:
		msg += " Faction %d is eliminated!" % defender_id
	_append_log(msg)
	_refresh()


func _on_game_over(_winner_id: int) -> void:
	_refresh()


func _append_log(text: String) -> void:
	log_label.append_text(text + "\n")


## ---------------------------------------------------------------------------
## Bottom banner: a Total War-style HUD strip (city info panel, buildings
## row, end-turn ribbon) built once here and then just refreshed with new
## label text/colors on every turn/battle event. Positions are anchor
## fractions of the full viewport, measured off a reference screenshot, so
## the banner keeps its on-screen proportions across window sizes.
## ---------------------------------------------------------------------------


func _style_box(
	bg: Color, border_color: Color = Color.TRANSPARENT, border_w: int = 0
) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	if border_w > 0:
		sb.set_border_width_all(border_w)
		sb.border_color = border_color
	return sb


func _set_font(control: Control, font: Font, size: int = -1) -> void:
	control.add_theme_font_override("font", font)
	if size > 0:
		control.add_theme_font_size_override("font_size", size)


func _anchor_rect(control: Control, left: float, top: float, right: float, bottom: float) -> void:
	control.anchor_left = left
	control.anchor_top = top
	control.anchor_right = right
	control.anchor_bottom = bottom
	control.offset_left = 0
	control.offset_top = 0
	control.offset_right = 0
	control.offset_bottom = 0


func _build_bottom_banner() -> void:
	bottom_banner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_build_city_panel()
	_build_buildings_panel()
	_build_end_turn_banner()


func _build_city_panel() -> void:
	var panel := Control.new()
	panel.name = "CityPanel"
	_anchor_rect(panel, 0.017, 0.617, 0.187, 0.99)
	bottom_banner.add_child(panel)

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 0)
	vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	panel.add_child(vbox)

	# Header: navy bar with the city name and a faction-colored tab.
	var header := PanelContainer.new()
	header.add_theme_stylebox_override("panel", _style_box(HUD_BLUE))
	header.custom_minimum_size = Vector2(0, 40)
	vbox.add_child(header)

	var header_row := HBoxContainer.new()
	header_row.add_theme_constant_override("separation", 8)
	header.add_child(header_row)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 10)
	margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header_row.add_child(margin)

	_city_panel_name_label = Label.new()
	_city_panel_name_label.text = "Burgos"
	_set_font(_city_panel_name_label, FONT_BOLD, 20)
	_city_panel_name_label.add_theme_color_override("font_color", Color.WHITE)
	_city_panel_name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	margin.add_child(_city_panel_name_label)

	_city_panel_owner_tab = ColorRect.new()
	_city_panel_owner_tab.custom_minimum_size = Vector2(36, 40)
	_city_panel_owner_tab.color = Color.INDIAN_RED
	header_row.add_child(_city_panel_owner_tab)

	# Body: parchment background holding the governor row and stat list.
	var body := PanelContainer.new()
	body.add_theme_stylebox_override("panel", _style_box(HUD_CREAM))
	body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	vbox.add_child(body)

	var body_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		body_margin.add_theme_constant_override("margin_%s" % side, 10)
	body.add_child(body_margin)

	var body_vbox := VBoxContainer.new()
	body_vbox.add_theme_constant_override("separation", 6)
	body_margin.add_child(body_vbox)

	var gov_row := HBoxContainer.new()
	gov_row.add_theme_constant_override("separation", 10)
	gov_row.custom_minimum_size = Vector2(0, 60)
	body_vbox.add_child(gov_row)

	var gov_button := PanelContainer.new()
	gov_button.add_theme_stylebox_override("panel", _style_box(Color(0.6, 0.6, 0.58)))
	gov_button.custom_minimum_size = Vector2(60, 60)
	gov_row.add_child(gov_button)

	var gov_label := Label.new()
	gov_label.text = "Gov..."
	_set_font(gov_label, FONT_MEDIUM, 13)
	gov_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	gov_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	gov_label.vertical_alignment = VERTICAL_ALIGNMENT_TOP
	gov_button.add_child(gov_label)

	var perk_label := Label.new()
	perk_label.text = "Perk pts   0"
	_city_panel_perk_label = perk_label
	_set_font(perk_label, FONT_SEMIBOLD, 15)
	perk_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	perk_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	perk_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gov_row.add_child(perk_label)

	var menu_pill := PanelContainer.new()
	var pill_sb := _style_box(HUD_BLUE)
	pill_sb.corner_radius_top_left = 14
	pill_sb.corner_radius_top_right = 14
	pill_sb.corner_radius_bottom_left = 14
	pill_sb.corner_radius_bottom_right = 14
	menu_pill.add_theme_stylebox_override("panel", pill_sb)
	menu_pill.custom_minimum_size = Vector2(36, 28)
	var menu_label := Label.new()
	menu_label.text = "☰"
	menu_label.add_theme_color_override("font_color", Color.WHITE)
	menu_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	menu_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	menu_pill.add_child(menu_label)
	gov_row.add_child(menu_pill)

	var stats_list := VBoxContainer.new()
	stats_list.add_theme_constant_override("separation", 4)
	body_vbox.add_child(stats_list)

	for i in STAT_ROWS.size():
		var row_def: Array = STAT_ROWS[i]
		# A visual gap before "Region wea..." matches the reference, which
		# groups income-adjacent stats apart from the population stats above.
		if row_def[0] == "region_wealth":
			var spacer := Control.new()
			spacer.custom_minimum_size = Vector2(0, 10)
			stats_list.add_child(spacer)
		stats_list.add_child(_make_stat_row(row_def[0], row_def[1], row_def[2]))


func _make_stat_row(key: String, label_text: String, icon: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)

	var icon_label := Label.new()
	icon_label.text = icon
	icon_label.add_theme_color_override("font_color", HUD_BLUE)
	icon_label.custom_minimum_size = Vector2(20, 0)
	row.add_child(icon_label)

	var name_label := Label.new()
	name_label.text = label_text
	_set_font(name_label, FONT_MEDIUM)
	name_label.add_theme_color_override("font_color", Color(0.25, 0.25, 0.25))
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(name_label)

	var value_label := Label.new()
	value_label.text = "0"
	value_label.add_theme_color_override("font_color", Color(0.05, 0.05, 0.05))
	_set_font(value_label, FONT_BOLD, 15)
	value_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	row.add_child(value_label)
	_city_stat_value_labels[key] = value_label

	return row


func _build_buildings_panel() -> void:
	var panel := Control.new()
	panel.name = "BuildingsPanel"
	_anchor_rect(panel, 0.19, 0.824, 0.762, 0.99)
	bottom_banner.add_child(panel)

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 0)
	vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	panel.add_child(vbox)

	var tabs_row := HBoxContainer.new()
	tabs_row.add_theme_constant_override("separation", 2)
	tabs_row.custom_minimum_size = Vector2(0, 34)
	vbox.add_child(tabs_row)
	tabs_row.add_child(_make_tab_button("Buildings", true))
	tabs_row.add_child(_make_tab_button("Characters", false))
	tabs_row.add_child(_make_tab_button("Military", false))

	var cards_row := PanelContainer.new()
	cards_row.add_theme_stylebox_override("panel", _style_box(HUD_CREAM))
	cards_row.size_flags_vertical = Control.SIZE_EXPAND_FILL
	vbox.add_child(cards_row)

	var cards_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		cards_margin.add_theme_constant_override("margin_%s" % side, 4)
	cards_row.add_child(cards_margin)

	var cards_hbox := HBoxContainer.new()
	cards_hbox.add_theme_constant_override("separation", 4)
	cards_margin.add_child(cards_hbox)

	for b in BUILDINGS:
		cards_hbox.add_child(_make_building_card(b["name"], b["level"], false))
	for name in LOCKED_BUILDINGS:
		cards_hbox.add_child(_make_building_card(name, -1, true))

	var red_strip := PanelContainer.new()
	red_strip.add_theme_stylebox_override("panel", _style_box(HUD_MAROON))
	red_strip.custom_minimum_size = Vector2(56, 0)
	cards_hbox.add_child(red_strip)

	var red_vbox := VBoxContainer.new()
	red_vbox.alignment = BoxContainer.ALIGNMENT_CENTER
	red_vbox.add_theme_constant_override("separation", 8)
	red_vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	red_strip.add_child(red_vbox)
	for glyph in ["🏰", "⛵"]:
		var icon_circle := PanelContainer.new()
		var circle_sb := _style_box(Color(0.15, 0.15, 0.15))
		circle_sb.corner_radius_top_left = 18
		circle_sb.corner_radius_top_right = 18
		circle_sb.corner_radius_bottom_left = 18
		circle_sb.corner_radius_bottom_right = 18
		icon_circle.add_theme_stylebox_override("panel", circle_sb)
		icon_circle.custom_minimum_size = Vector2(36, 36)
		var glyph_label := Label.new()
		glyph_label.text = glyph
		glyph_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		glyph_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		glyph_label.add_theme_color_override("font_color", Color.WHITE)
		icon_circle.add_child(glyph_label)
		red_vbox.add_child(icon_circle)


func _make_tab_button(text: String, active: bool) -> PanelContainer:
	var tab := PanelContainer.new()
	tab.add_theme_stylebox_override("panel", _style_box(HUD_BLUE if active else HUD_BLUE_DARK))
	var margin := MarginContainer.new()
	for side in ["left", "right"]:
		margin.add_theme_constant_override("margin_%s" % side, 14)
	tab.add_child(margin)
	var label := Label.new()
	label.text = text
	_set_font(label, FONT_SEMIBOLD)
	label.add_theme_color_override("font_color", Color.WHITE)
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	margin.add_child(label)
	return tab


func _make_building_card(name: String, level: int, locked: bool) -> VBoxContainer:
	var card := VBoxContainer.new()
	card.add_theme_constant_override("separation", 2)
	card.custom_minimum_size = Vector2(84, 0)

	var image_panel := PanelContainer.new()
	image_panel.add_theme_stylebox_override(
		"panel", _style_box(Color(0.7, 0.7, 0.7) if locked else Color.WHITE, Color.BLACK, 1)
	)
	image_panel.custom_minimum_size = Vector2(0, 70)
	card.add_child(image_panel)

	if not locked:
		var level_label := Label.new()
		level_label.text = "Lv.%d" % level
		_set_font(level_label, FONT_BOLD, 13)
		level_label.add_theme_color_override("font_color", Color.BLACK)
		image_panel.add_child(level_label)
	elif name == "Mine":
		var q_label := Label.new()
		q_label.text = "?"
		_set_font(q_label, FONT_BOLD, 28)
		q_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		q_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		q_label.add_theme_color_override("font_color", Color(0.1, 0.1, 0.1))
		image_panel.add_child(q_label)

	var caption := Label.new()
	caption.text = name
	_set_font(caption, FONT_SEMIBOLD)
	caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	caption.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	card.add_child(caption)

	return card


func _build_end_turn_banner() -> void:
	end_turn_button = Button.new()
	end_turn_button.name = "EndTurnButton"
	_anchor_rect(end_turn_button, 0.89, 0.87, 0.965, 0.93)
	end_turn_button.text = "END TURN 1"
	end_turn_button.add_theme_color_override("font_color", Color.WHITE)
	_set_font(end_turn_button, FONT_BOLD, 16)
	var sb := _style_box(Color(0.55, 0.13, 0.05), Color(0.85, 0.45, 0.1), 2)
	end_turn_button.add_theme_stylebox_override("normal", sb)
	end_turn_button.add_theme_stylebox_override(
		"hover", _style_box(Color(0.65, 0.18, 0.07), Color(0.85, 0.45, 0.1), 2)
	)
	end_turn_button.add_theme_stylebox_override(
		"pressed", _style_box(Color(0.45, 0.1, 0.04), Color(0.85, 0.45, 0.1), 2)
	)
	bottom_banner.add_child(end_turn_button)
	end_turn_button.pressed.connect(_on_end_turn_pressed)


func _format_stat(n: int) -> String:
	if n >= 1000:
		return "%.1fK" % (n / 1000.0)
	return str(n)


## Picks which city's data the banner shows: the first city owned by whoever
## is acting this turn, falling back to city 0 if that faction somehow holds
## none (shouldn't happen - a faction with no cities is eliminated).
func _refresh_bottom_banner(state: Dictionary) -> void:
	var current_faction: int = state["current_faction"]
	var shown_city: Dictionary = {}
	if _selected_city_id != -1:
		for city in state["cities"]:
			if int(city["id"]) == _selected_city_id:
				shown_city = city
				break
	if shown_city.is_empty():
		for city in state["cities"]:
			if int(city["owner"]) == current_faction:
				shown_city = city
				break
	if shown_city.is_empty() and not state["cities"].is_empty():
		shown_city = state["cities"][0]
	if shown_city.is_empty():
		return

	_city_panel_name_label.text = shown_city["name"]
	_city_panel_owner_tab.color = FACTION_COLORS[int(shown_city["owner"]) % FACTION_COLORS.size()]

	var income: int = int(shown_city["income"])
	for row_def in STAT_ROWS:
		var key: String = row_def[0]
		var multiplier: int = row_def[3]
		_city_stat_value_labels[key].text = _format_stat(income * multiplier)

	end_turn_button.text = "END TURN %d" % int(state["turn"])
	end_turn_button.disabled = bool(state["game_over"])
