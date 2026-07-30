extends Node2D
## Thin GDScript glue: loads the region-polygon political map (province_map.gd,
## a port of Thomas Holtvedt's grand-strategy-simple tutorial), derives one
## city per map region for the Rust CampaignManager, and sets up a pannable/
## zoomable 2D "camera" over it (WASD/edge pan, scroll zoom). City markers/
## dropdown/end-turn button render state on every signal instead of polling -
## unchanged from before this script also owned world/camera setup.

const ArmyLayer := preload("res://campaign/army_layer.gd")
const ProvinceMap := preload("res://campaign/province_map.gd")
const MAX_TURNS := 10

# The map is Thomas Holtvedt's grand-strategy-simple region bitmap (a
# Scandinavia/Baltic political sketch), loaded at its native 100x100 pixel
# size by province_map.gd. MAP_SCALE blows that up into world units so pan
# speed/zoom/army movement feel the same as before the source image's own
# tiny pixel grid would otherwise imply.
const MAP_SCALE := 20.0

# Pan/zoom tuning. _base_ppu is the pixels-per-world-unit at zoom 1.0, kept in
# sync with the viewport's larger dimension every frame the window resizes
# (see _update_camera_transform) - a "cover" fit rather than a "contain" fit,
# so the map fills the whole frame edge-to-edge at startup (Total War-style)
# instead of leaving empty background letterboxed around it. The scroll
# wheel/Z/X keys zoom in from there.
var _base_ppu: float = 1.0
const PAN_SPEED := 0.6  # fraction of map_extent per second, at zoom 1.0
const ZOOM_STEP := 0.1  # per scroll-wheel notch
const ZOOM_KEY_SPEED := 1.2  # zoom units/sec while Z/X is held
# _base_ppu is a cover-fit (map exactly fills the frame at zoom 1.0), so
# zooming out below that would reveal empty background past the map's edges -
# 1.0 is as far out as the camera goes.
const MIN_ZOOM := 1.0
const MAX_ZOOM := 2.2

# Faction colors are declared by the map package (factions.json) and read in
# _ready(), so the palette lives with the scenario rather than in this script.
# Fallback only, if a package ships no roster.
const DEFAULT_FACTION_COLORS: Array[Color] = [
	Color.INDIAN_RED, Color.CORNFLOWER_BLUE, Color.MEDIUM_SEA_GREEN, Color.GOLDENROD
]

# Armies. The player is faction 0; every other faction is played by the Rust
# side's random AI when its turn comes round (see _run_ai_factions). The pieces
# themselves live in campaign/army_layer.gd.
const PLAYER_FACTION := 0
const AI_STEP_SECONDS := 0.35  # pause between AI factions, so turns stay legible

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
@onready var world_layer: Node2D = $WorldLayer
@onready var status_label: Label = $UI/StatusLabel
@onready var log_label: RichTextLabel = $UI/LogLabel
@onready var target_option: OptionButton = $UI/Controls/TargetOption
@onready var attack_button: Button = $UI/Controls/AttackButton
@onready var bottom_banner: Control = $UI/BottomBanner
@onready var cities_root: Control = $CityMarkers

var end_turn_button: Button

var city_markers: Dictionary = {}

var _province_map: Node2D
var _city_positions: PackedVector2Array = []
## Province ids, parallel to _city_positions.
var _province_ids: Array = []
var _faction_colors: Array[Color] = DEFAULT_FACTION_COLORS.duplicate()
var _marker_positions: Dictionary = {}
var _map_extent: float = 0.0
## True per-axis half-width/half-height of the map in world units - unlike
## _map_extent (which collapses to a single scalar for pan clamping), this
## keeps the map's actual aspect ratio so the cover-fit in
## _update_camera_transform doesn't under-fill a non-square map's shorter
## axis and expose the viewport's background past its edge.
var _map_half_size := Vector2.ZERO
var _cam_focus := Vector2.ZERO
var _cam_zoom := 1.0
var _selected_city_id: int = -1

var _army_layer: Control
var _ai_running := false

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

	_province_map = ProvinceMap.new()
	_province_map.name = "ProvinceMap"
	world_layer.add_child(_province_map)
	if not _province_map.setup():
		_fail_to_start("could not load the map package in campaign/map_data")
		return
	_province_map.region_clicked.connect(_on_region_clicked)

	# The faction roster and its colors come from the map package now, so a
	# scenario can add or recolor factions without touching this script.
	_faction_colors = _province_map.package.faction_colors()

	# Centre the package's top-left-origin pixel space on the world origin and
	# blow it up by MAP_SCALE, so the rest of this script's pan/zoom/army-
	# clamping math (which all assumes a map centred on
	# [-_map_extent, _map_extent]) doesn't have to know about map pixels.
	var half_size: Vector2 = _province_map.map_size * MAP_SCALE / 2.0
	_map_extent = maxf(half_size.x, half_size.y)
	_map_half_size = half_size
	_province_map.position = -half_size
	_province_map.scale = Vector2.ONE * MAP_SCALE

	# Province centroids are in map pixels; the simulation works in world
	# units, so convert once here and hand the table over in world space.
	_province_ids = []
	_city_positions = PackedVector2Array()
	for province_id in _province_map.province_centers:
		_province_ids.append(province_id)
		var local_pos: Vector2 = _province_map.province_centers[province_id]
		_city_positions.append(local_pos * MAP_SCALE - half_size)
	if _city_positions.is_empty():
		_fail_to_start("the map package has no provinces - nothing to play")
		return

	_army_layer = ArmyLayer.new()
	_army_layer.name = "ArmyMarkers"
	add_child(_army_layer)
	_army_layer.setup(manager, _world_to_screen, _screen_to_world, _faction_colors)
	_army_layer.log_message.connect(_append_log)
	_army_layer.state_changed.connect(_refresh)

	# City markers must not swallow the clicks that become move orders; their
	# own child markers keep taking clicks regardless of the container filter.
	cities_root.mouse_filter = Control.MOUSE_FILTER_IGNORE

	# Close-in TW-style campaign framing: start centred over the cities'
	# midpoint rather than the map origin.
	var centre := Vector2.ZERO
	for c in _city_positions:
		centre += c
	centre /= _city_positions.size()
	_cam_focus = centre
	_cam_zoom = 1.0
	_update_camera_transform()

	manager.turn_started.connect(_on_turn_started)
	manager.battle_resolved.connect(_on_battle_resolved)
	manager.game_over.connect(_on_game_over)
	attack_button.pressed.connect(_on_attack_pressed)

	# Armies are clamped to the map extent, so a random AI walk can't march
	# off the edge of the world. Must be set before the game starts.
	manager.set_map_extent(_map_extent)

	# start_game_from_provinces() emits turn_started synchronously, so
	# _refresh() can run before this function returns - city markers must
	# already exist by then. _ensure_city_marker() below makes marker
	# creation lazy so there is no ordering requirement between the two.
	manager.load_factions(_province_map.package.factions)
	manager.load_provinces(_world_space_province_table(half_size))
	manager.start_game_from_provinces(MAX_TURNS)
	_project_markers()
	_refresh()
	get_viewport().size_changed.connect(_on_viewport_resized)

	# --resolution/window-manager resizes don't land in get_viewport_rect()
	# within the same frame that requests them, so a transform computed here
	# synchronously can be centred on a stale (e.g. project-default) rect.
	# One deferred re-run after the window has actually settled fixes that
	# without waiting on a signal that may or may not fire.
	await get_tree().process_frame
	_update_camera_transform()
	_project_markers()


## Recomputes world_layer's position/scale from _cam_focus/_cam_zoom - this
## stands in for what a Camera2D would do, without needing an actual camera
## node, since city/army markers project manually via _world_to_screen.
## Uses get_viewport_rect().size (not the fixed content_scale_size) so this
## stays correct under CONTENT_SCALE_ASPECT_EXPAND, which reveals more than
## 1280x800 on wider/taller windows - the same rect every Control here
## anchors against. Must be re-run whenever that rect actually changes (see
## the size_changed wiring in _ready()), since --resolution/window resizes
## don't take effect inside the same frame that requests them.
func _update_camera_transform() -> void:
	var viewport_size := get_viewport_rect().size
	_base_ppu = maxf(
		viewport_size.x / (2.0 * _map_half_size.x), viewport_size.y / (2.0 * _map_half_size.y)
	)
	var pixels_per_unit := _base_ppu / _cam_zoom
	var viewport_center := viewport_size / 2.0
	world_layer.position = viewport_center - _cam_focus * pixels_per_unit
	world_layer.scale = Vector2.ONE * pixels_per_unit


func _on_viewport_resized() -> void:
	_update_camera_transform()
	_project_markers()


## World-space point for a screen pixel, from world_layer's current
## position/scale. Returns Vector2.INF only theoretically (scale never 0).
func _screen_to_world(screen: Vector2) -> Vector2:
	if world_layer.scale.x == 0.0:
		return Vector2.INF
	return (screen - world_layer.position) / world_layer.scale


func _world_to_screen(world: Vector2) -> Vector2:
	return world_layer.position + world * world_layer.scale


func _process(delta: float) -> void:
	var move := Vector2.ZERO
	if Input.is_key_pressed(KEY_W):
		move.y -= 1.0
	if Input.is_key_pressed(KEY_S):
		move.y += 1.0
	if Input.is_key_pressed(KEY_A):
		move.x -= 1.0
	if Input.is_key_pressed(KEY_D):
		move.x += 1.0

	var changed := false

	if move != Vector2.ZERO:
		move = move.normalized()
		_cam_focus = (_cam_focus + move * PAN_SPEED * _map_extent * _cam_zoom * delta).limit_length(
			_map_extent
		)
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
		elif event.button_index == MOUSE_BUTTON_LEFT:
			# Left-click on open map = "march there". A left-click that
			# reached here missed every marker; if an army is selected this
			# is a move order, otherwise it's a deselect.
			if _army_layer.selected_army_id() != -1:
				_army_layer.order_selected_at_screen(event.position)
			else:
				_army_layer.select(-1)
		elif event.button_index == MOUSE_BUTTON_RIGHT:
			# Right-click on open map does nothing; right-click only attacks
			# via the marker handler in army_layer._on_marker_input.
			pass


## Screen position of every city, from its world position through the
## current pan/zoom. Re-run whenever that changes (see _process/
## _unhandled_input above) as well as once at startup and on resize.
func _project_markers() -> void:
	for i in _city_positions.size():
		var pos := _world_to_screen(_city_positions[i])
		_marker_positions[i] = pos
		if city_markers.has(i):
			var marker: ColorRect = city_markers[i]
			marker.position = pos - marker.size / 2.0
	if _army_layer != null:
		_army_layer.project()


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


## Clicking a province polygon mirrors _on_city_marker_input: selects the city
## standing in that province, exactly as clicking its marker would. Matched by
## province id rather than by name, so two places can share a name.
func _on_region_clicked(province_id: int) -> void:
	var state: Dictionary = manager.get_state()
	for city in state["cities"]:
		if int(city.get("province", -1)) == province_id:
			_selected_city_id = int(city["id"])
			for i in target_option.item_count:
				if target_option.get_item_id(i) == _selected_city_id:
					target_option.select(i)
					break
			_refresh_bottom_banner(state)
			return


## The province table as the simulation wants it: same rows the map package
## shipped, but with centroids converted from map pixels into world units so
## Rust never has to know what a pixel is.
func _world_space_province_table(half_size: Vector2) -> Array:
	var rows: Array = []
	for province in _province_map.package.provinces:
		var row: Dictionary = province.duplicate(true)
		var centroid: Array = province.get("centroid", [0, 0])
		var world := Vector2(centroid[0], centroid[1]) * MAP_SCALE - half_size
		row["centroid"] = [world.x, world.y]
		rows.append(row)
	return rows


func _refresh() -> void:
	var state: Dictionary = manager.get_state()
	_apply_province_ownership(state)

	for city in state["cities"]:
		var marker: ColorRect = _ensure_city_marker(city)
		marker.color = _faction_colors[int(city["owner"]) % _faction_colors.size()]

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
	var selected_army: int = _army_layer.selected_army_id()
	if selected_army != -1:
		for army in state["armies"]:
			if int(army["id"]) == selected_army:
				lines.append(
					(
						"[%s] %d / %d move points"
						% [army["name"], int(army["movement"]), int(army["max_movement"])]
					)
				)
	elif int(state["current_faction"]) == PLAYER_FACTION:
		lines.append(
			"Click an army to select, left-click the map to march, right-click an enemy to attack."
		)
	status_label.text = "\n".join(lines)

	_army_layer.sync(state)
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
	if _ai_running:
		return
	_army_layer.select(-1)
	manager.end_turn()
	_run_ai_factions()


## Repaints the map from the simulation's current ownership. Nothing on disk
## changes when a province is conquered - this is where it changes color.
func _apply_province_ownership(state: Dictionary) -> void:
	var owner_by_province: Dictionary = {}
	for province in state.get("provinces", []):
		owner_by_province[int(province["id"])] = int(province["owner"])
	if not owner_by_province.is_empty():
		_province_map.apply_ownership(owner_by_province, _faction_colors)


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


## Plays every AI faction in sequence after the player ends their turn,
## pausing between them so a whole round of army moves is watchable instead of
## resolving in a single frame. Player orders are locked out for the duration.
func _run_ai_factions() -> void:
	_ai_running = true
	_army_layer.set_orders_locked(true)
	end_turn_button.disabled = true

	while not manager.is_game_over() and manager.current_faction_id() != PLAYER_FACTION:
		manager.run_ai_turn()
		_refresh()
		await get_tree().create_timer(ArmyLayer.MOVE_SECONDS + AI_STEP_SECONDS).timeout
		manager.end_turn()

	_ai_running = false
	_army_layer.set_orders_locked(false)
	_refresh()


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
	_city_panel_owner_tab.color = _faction_colors[int(shown_city["owner"]) % _faction_colors.size()]

	var income: int = int(shown_city["income"])
	for row_def in STAT_ROWS:
		var key: String = row_def[0]
		var multiplier: int = row_def[3]
		_city_stat_value_labels[key].text = _format_stat(income * multiplier)

	end_turn_button.text = "END TURN %d" % int(state["turn"])
	end_turn_button.disabled = bool(state["game_over"]) or _ai_running
