extends Node2D
## Thin GDScript glue: loads the region-polygon political map (province_map.gd,
## a port of Thomas Holtvedt's grand-strategy-simple tutorial), derives one
## city per map region for the Rust CampaignManager, and sets up a pannable/
## zoomable 2D "camera" over it (WASD/edge pan, scroll zoom). City markers/
## dropdown/end-turn button render state on every signal instead of polling -
## unchanged from before this script also owned world/camera setup.

const ArmyLayer := preload("res://campaign/army_layer.gd")
const HudBuilder := preload("res://campaign/campaign_hud_builder.gd")
const ProvinceMap := preload("res://campaign/province_map.gd")
const CityMarker := preload("res://campaign/city_marker.gd")
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
# pixels_per_unit is _base_ppu / _cam_zoom, so _cam_zoom is an *inverse* zoom
# factor - lower means more magnified. _base_ppu is a cover-fit (map exactly
# fills the frame when _cam_zoom is 1.0), so 1.0 is as far as _cam_zoom can
# rise: going past it shrinks pixels_per_unit below the cover-fit ratio and
# reveals empty background past the map's edges (X, or scroll-down, raises
# _cam_zoom - "zooming out" was doing exactly that before this was fixed).
# The lower bound is how far in Z/scroll-up can magnify.
const MIN_ZOOM := 1.0 / 2.2
const MAX_ZOOM := 1.0

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

# Total War-style "watch the AI move" camera: while an AI faction's turn is
# resolving, the camera pans/zooms in on each army_moved signal it produces in
# turn, rather than sitting still while enemy armies teleport around
# off-screen. AI_CAMERA_ZOOM is a _cam_zoom value (inverse magnification, see
# the comment above _cam_zoom below) - lower than MAX_ZOOM (1.0) but not as
# tight as MIN_ZOOM, so the move is legible without losing surrounding context.
const AI_CAMERA_ZOOM := 0.6
const AI_CAMERA_PAN_SECONDS := 0.45

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
@onready var bottom_banner: Control = $UI/BottomBanner
@onready var _top_bar: Control = $UI/TopBar
@onready var _turn_indicator: Control = $UI/TurnIndicator
@onready var cities_root: Control = $CityMarkers

var end_turn_button: Button

var city_markers: Dictionary = {}

var _province_map: Node2D
var _city_positions: PackedVector2Array = []
## Province ids, parallel to _city_positions.
var _province_ids: Array = []
var _faction_colors: Array[Color] = DEFAULT_FACTION_COLORS.duplicate()
var _marker_positions: Dictionary = {}
## City id (as assigned by the Rust campaign state) -> world position, taken
## straight from state["cities"] each _refresh(). Provinces with no starting
## owner get no city in Rust (start_game_from_provinces), so city ids are a
## *compacted* subset of the province array's indices - keying marker
## placement off the province-array loop index, as _project_markers() used
## to, silently misaligns a marker's screen position (and therefore its
## click target) the moment any province in the list is unowned. Populated
## in _refresh(); read by _project_markers() and _ensure_city_marker() so
## every marker is placed, and therefore clicked, by its own real city id.
var _city_world_positions: Dictionary = {}
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

# AI-turn camera watch. `_watching_ai_moves` is true only for the duration of
# a single `manager.run_ai_turn()` call (see _run_ai_factions); the
# `army_moved` signal fires for player moves too (order_selected_to), so this
# flag - rather than checking the mover's faction - is what keeps the camera
# from hijacking the player's own orders. `_ai_moves` collects every move
# `run_ai_turn()` reports (it plays every army the faction owns in one call,
# so several can land before the signal handler's caller gets control back).
var _watching_ai_moves := false
var _ai_moves: Array = []

# Bottom-banner widgets that get new data every _refresh(); built once in
# _build_bottom_banner() and then just written into on each turn/battle event.
var _city_panel_name_label: Label
var _city_panel_owner_tab: ColorRect
var _city_panel_perk_label: Label
var _city_stat_value_labels: Dictionary = {}

# The settlement panel (city info + buildings/military tray) is the
# selection-dependent half of the bottom banner - hidden whenever no
# settlement is selected so only the core/persistent HUD (top bar, end-turn
# ribbon) shows. Refs stored here so _refresh_bottom_banner/
# _on_settlement_panel_close_pressed can toggle both halves together.
var _city_panel: Control
var _buildings_panel: Control

# Settlement-panel tab content (Buildings/Military) - built once by
# HudBuilder.build_bottom_banner(), swapped in/out by _on_settlement_tab.
var _buildings_content: Control
var _military_content: Control

# Military tab: stubbed unit rows, purely for visual layout - not backed by
# any real army/garrison simulation state yet.
const ARMY_UNITS := [
	{"name": "Swordsmen", "icon": "⚔", "count": 80},
	{"name": "Spearmen", "icon": "🛡", "count": 120},
	{"name": "Longbowmen", "icon": "🏹", "count": 60},
	{"name": "Mounted Knights", "icon": "🐎", "count": 40},
]
const GARRISON_UNITS := [
	{"name": "Town Militia", "icon": "🪓", "count": 100},
	{"name": "Town Watch", "icon": "🛡", "count": 50},
	{"name": "Crossbowmen", "icon": "🏹", "count": 30},
]

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

# Top-bar resource cluster (money/deficit/food/season/year): render-only
# placeholders, [icon glyph, label, display value] - not wired to any real
# economy state yet.
const TOP_BAR_STATS := [
	["🪙", "Treasury", "10,000"],
	["📉", "Deficit", "-250"],
	["🌾", "Food", "+4.0K"],
	["☀", "Season", "Summer"],
	["📅", "Year", "395 AD"],
]
const TOP_BAR_PLACEHOLDER_ICON_COUNT := 3

# Top-bar widgets refreshed alongside the bottom banner.
var _top_bar_stat_value_labels: Dictionary = {}
var settlements_button: Button
var armies_button: Button
var wiki_button: Button
var log_button: Button
var log_label: RichTextLabel
var _log_panel: Control

# Turn indicator (top-middle of screen): a circular placeholder icon with
# the current faction's name below it, built once by
# HudBuilder.build_turn_indicator() and rewritten every _refresh() to track
# state["current_faction"].
var _turn_indicator_name_label: Label


## Swaps which settlement-panel tab content is visible (Buildings/Military).
## Wired to each tab button's `toggled` signal by HudBuilder.
func _on_settlement_tab_selected(tab_name: String) -> void:
	_buildings_content.visible = tab_name == "Buildings"
	_military_content.visible = tab_name == "Military"


## Closes the settlement panel (back button in the city panel header):
## clears the selection and re-runs the banner refresh, which hides the
## city/buildings panels and leaves only the core HUD visible - see
## _refresh_bottom_banner.
func _on_settlement_panel_close_pressed() -> void:
	_selected_city_id = -1
	_refresh_bottom_banner(manager.get_state())


func _fail_to_start(message: String) -> void:
	printerr("error: ", message)
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

	# UI/BottomBanner/TopBar are full-rect-anchored Controls sitting directly
	# under this Node2D (not under another Control), so Godot only recomputes
	# their anchor-derived size on a viewport *resize* notification. The
	# viewport is already at its final size before this scene's first frame
	# (--resolution / content_scale_size are both set before load), so that
	# notification never fires and every anchored Control here is stuck at
	# size (0, 0) forever - the whole bottom banner and top bar render
	# shrunk into a sliver pinned at the top-left instead of covering the
	# screen. Priming size explicitly once (mirrored in
	# _on_viewport_resized for real resizes) fixes that.
	_sync_ui_root_size()
	HudBuilder.build_top_bar(self)
	HudBuilder.build_bottom_banner(self)
	HudBuilder.build_turn_indicator(self)

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
	for province_id in _province_map.city_positions:
		_province_ids.append(province_id)
		var local_pos: Vector2 = _province_map.city_positions[province_id]
		_city_positions.append(local_pos * MAP_SCALE - half_size)
	if _city_positions.is_empty():
		_fail_to_start("the map package has no provinces - nothing to play")
		return

	_army_layer = ArmyLayer.new()
	_army_layer.name = "ArmyMarkers"
	add_child(_army_layer)
	_army_layer.setup(manager, _world_to_screen, _screen_to_world, _faction_colors, _province_map)
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
	manager.army_moved.connect(_on_army_moved_for_camera)
	log_button.pressed.connect(_on_log_button_pressed)

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
##
## _cam_focus is clamped here (rather than only where it's set/panned) so
## every source of change - initial framing on the cities' midpoint, WASD
## pan, and zoom itself changing how much world the viewport covers - funnels
## through one place. Without it, an off-centre starting focus or a pan/zoom
## near a corner can push the map's edge inside the viewport and expose the
## empty grey background past it, even though MIN_ZOOM=1.0 is meant to be as
## far out as the camera goes.
func _update_camera_transform() -> void:
	var viewport_size := get_viewport_rect().size
	_base_ppu = maxf(
		viewport_size.x / (2.0 * _map_half_size.x), viewport_size.y / (2.0 * _map_half_size.y)
	)
	var pixels_per_unit := _base_ppu / _cam_zoom
	_cam_focus = _clamp_cam_focus(_cam_focus, _map_half_size, viewport_size, pixels_per_unit)
	var viewport_center := viewport_size / 2.0
	world_layer.position = viewport_center - _cam_focus * pixels_per_unit
	world_layer.scale = Vector2.ONE * pixels_per_unit


## Clamps a camera focus so the map's edge never falls short of the
## viewport's edge on either axis. Pure geometry (no node state) so it can be
## tested directly: half of the viewport, in world units at the given
## pixels_per_unit, is how far off-centre the focus can sit before that axis's
## near edge of the map would clear the viewport.
static func _clamp_cam_focus(
	focus: Vector2, map_half_size: Vector2, viewport_size: Vector2, pixels_per_unit: float
) -> Vector2:
	var half_viewport_world := (viewport_size / 2.0) / pixels_per_unit
	var max_focus := Vector2(
		maxf(map_half_size.x - half_viewport_world.x, 0.0),
		maxf(map_half_size.y - half_viewport_world.y, 0.0)
	)
	return Vector2(
		clampf(focus.x, -max_focus.x, max_focus.x), clampf(focus.y, -max_focus.y, max_focus.y)
	)


func _on_viewport_resized() -> void:
	_sync_ui_root_size()
	_update_camera_transform()
	_project_markers()


## See the comment in _ready() above: full-rect anchored Controls whose
## parent isn't itself a Control never pick up an anchor-driven resize on
## their own here, so this pokes their `size` directly to the current
## viewport rect. Anchors still do the proportional work for every child
## underneath - this just keeps the *root* Controls themselves from being
## stuck at (0, 0).
func _sync_ui_root_size() -> void:
	var viewport_size := get_viewport_rect().size
	$UI.set_deferred("size", viewport_size)
	bottom_banner.set_deferred("size", viewport_size)
	if _top_bar != null:
		_top_bar.set_deferred("size", viewport_size)
	if _turn_indicator != null:
		_turn_indicator.set_deferred("size", viewport_size)
	cities_root.set_deferred("size", viewport_size)


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
		# Precisely clamped in _update_camera_transform below, which knows the
		# current viewport/zoom; this only needs to move the focus, not bound it.
		_cam_focus += move * PAN_SPEED * _map_extent * _cam_zoom * delta
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
			# A left-click that reaches here missed every province polygon
			# (each one has its own Area2D that would otherwise have
			# consumed it via _on_region_clicked) and every army/city
			# marker - i.e. it landed on water/impassable terrain or
			# outside the map entirely. That's never a valid move or
			# selection target, so clear whatever's currently selected
			# (army and/or city panel) instead of issuing an order onto an
			# unmovable spot.
			_army_layer.select(-1)
			if _selected_city_id != -1:
				_selected_city_id = -1
				_refresh_bottom_banner(manager.get_state())
		elif event.button_index == MOUSE_BUTTON_RIGHT:
			# Right-click on open map does nothing; right-click only attacks
			# via the marker handler in army_layer._on_marker_input.
			pass


## Screen position of every *actual* city, from its world position (as
## reported by the Rust state, keyed by real city id in
## _city_world_positions) through the current pan/zoom. Re-run whenever that
## changes (see _process/_unhandled_input above) as well as once at startup
## and on resize.
##
## Deliberately iterates _city_world_positions/city_markers (real city ids)
## rather than _city_positions (one entry per province, including unowned
## ones with no city at all) - looping the latter by array index used to
## hand marker #i the position belonging to province #i regardless of
## whether city id i actually corresponded to it, which is what let a
## marker drawn with one city's name sit at another city's screen position
## and forward its click to the wrong id.
func _project_markers() -> void:
	for id in _city_world_positions:
		var pos := _world_to_screen(_city_world_positions[id])
		_marker_positions[id] = pos
		if city_markers.has(id):
			var marker: CityMarker = city_markers[id]
			marker.position = pos - marker.anchor_offset()
	if _army_layer != null:
		_army_layer.project()


func _ensure_city_marker(city: Dictionary) -> CityMarker:
	var id: int = int(city["id"])
	if city_markers.has(id):
		return city_markers[id]

	var marker := CityMarker.new()
	marker.setup(String(city["name"]))
	var world_pos: Vector2 = _city_world_positions.get(id, Vector2.ZERO)
	marker.position = _world_to_screen(world_pos) - marker.anchor_offset()
	marker.clicked.connect(_on_city_marker_clicked.bind(id))
	cities_root.add_child(marker)
	city_markers[id] = marker

	return marker


## Clicking a city marker selects it and shows it in the bottom info panel.
func _on_city_marker_clicked(city_id: int) -> void:
	_selected_city_id = city_id
	_refresh_bottom_banner(manager.get_state())


## Clicking a province polygon while an army is selected is a move order into
## that province - onto whichever city stands in it if there is one, or onto
## the province's own centroid otherwise, so land provinces with no city of
## their own are still reachable by clicking anywhere inside them. (Before
## this fallback, clicking such a province with an army selected fell
## straight through this function and issued no order at all, since it only
## ever matched provinces that had a city.) This is the only way to reach a
## bordering province that has no marker of its own under the mouse.
## Without a selected army it mirrors _on_city_marker_input: selects the city
## standing in that province, if any. Matched by province id rather than by
## name, so two places can share a name.
func _on_region_clicked(province_id: int) -> void:
	var state: Dictionary = manager.get_state()
	for city in state["cities"]:
		if int(city.get("province", -1)) != province_id:
			continue
		if _army_layer.selected_army_id() != -1:
			var world_pos: Vector2 = _city_world_positions.get(int(city["id"]), Vector2.INF)
			if world_pos != Vector2.INF:
				_army_layer.order_selected_to(world_pos)
			return
		_selected_city_id = int(city["id"])
		_refresh_bottom_banner(state)
		return

	# No city stands in this province: march the selected army to its
	# centroid instead of silently doing nothing.
	if _army_layer.selected_army_id() != -1:
		var center_px: Vector2 = _province_map.province_centers.get(province_id, Vector2.INF)
		if center_px == Vector2.INF:
			return
		var half_size: Vector2 = _province_map.map_size * MAP_SCALE / 2.0
		var world_pos: Vector2 = center_px * MAP_SCALE - half_size
		_army_layer.order_selected_to(world_pos)


## The province table as the simulation wants it: same rows the map package
## shipped, but with centroid AND city_position converted from map pixels into
## world units so Rust never has to know what a pixel is. Missing either
## conversion leaves that field in raw map-pixel space (tens to hundreds of
## units) next to a world space that spans +-tens of thousands of units - Rust
## would then site whatever reads that field within a few pixels of world
## origin instead of on the actual settlement, which is how starting armies
## for every faction ended up stacked on top of each other near the map's
## centre instead of at their own capitals.
func _world_space_province_table(half_size: Vector2) -> Array:
	var rows: Array = []
	for province in _province_map.package.provinces:
		var row: Dictionary = province.duplicate(true)
		var centroid: Array = province.get("centroid", [0, 0])
		row["centroid"] = _pixel_array_to_world(centroid, half_size)
		if province.has("city_position"):
			row["city_position"] = _pixel_array_to_world(province["city_position"], half_size)
		rows.append(row)
	return rows


## Converts a `[x, y]` map-pixel pair (as provinces.table.json stores centroid/
## city_position) into a `[x, y]` world-unit pair, per this script's
## MAP_SCALE/half_size convention.
func _pixel_array_to_world(pixel: Array, half_size: Vector2) -> Array:
	var world := Vector2(pixel[0], pixel[1]) * MAP_SCALE - half_size
	return [world.x, world.y]


func _refresh() -> void:
	var state: Dictionary = manager.get_state()
	_apply_province_ownership(state)

	# Real city id -> world position, straight from Rust (city ids are a
	# compacted subset of province indices, so this must be keyed by id, not
	# by position in state["cities"]). Rebuilt before _project_markers() so
	# any new marker below is placed at the position matching its own id.
	for city in state["cities"]:
		_city_world_positions[int(city["id"])] = Vector2(float(city["x"]), float(city["y"]))

	for city in state["cities"]:
		var marker: CityMarker = _ensure_city_marker(city)
		marker.set_faction_color(_faction_colors[int(city["owner"]) % _faction_colors.size()])

	_project_markers()
	_army_layer.sync(state)
	_refresh_bottom_banner(state)
	_refresh_turn_indicator(state)

	if state["game_over"]:
		end_turn_button.disabled = true
		var winner_id: int = state["winner"]
		var winner_name := "nobody"
		for faction in state["factions"]:
			if int(faction["id"]) == winner_id:
				winner_name = faction["name"]
		_append_log("[b]Game over. %s wins.[/b]" % winner_name)


func _on_log_button_pressed() -> void:
	_log_panel.visible = not _log_panel.visible


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
		_ai_moves.clear()
		_watching_ai_moves = true
		manager.run_ai_turn()
		_watching_ai_moves = false
		_refresh()
		await _focus_camera_on_ai_moves(_ai_moves)
		await get_tree().create_timer(AI_STEP_SECONDS).timeout
		manager.end_turn()

	_ai_running = false
	_army_layer.set_orders_locked(false)
	_refresh()


## Collects every army_moved signal fired while `_watching_ai_moves` is set -
## i.e. only the moves `run_ai_turn()` itself issues, never a player order
## (order_selected_to's move_army call happens outside that window). Signal
## handler, so its argument shape must match `army_moved`'s.
func _on_army_moved_for_camera(
	_army_id: int, from: Vector2, to: Vector2, _spent: float, _movement_left: float
) -> void:
	if not _watching_ai_moves:
		return
	_ai_moves.append({"from": from, "to": to})


## Pans/zooms the camera onto each AI move in turn (Total War-style "watch the
## enemy turn"), pausing on each long enough for army_layer's own move tween
## (ArmyLayer.MOVE_SECONDS) to actually land, then eases back to wherever the
## camera was sitting before this faction's turn started. A no-op if the
## faction made no moves (e.g. every army was already immobile).
func _focus_camera_on_ai_moves(moves: Array) -> void:
	if moves.is_empty():
		return
	var start_focus := _cam_focus
	var start_zoom := _cam_zoom

	for move in moves:
		var from: Vector2 = move["from"]
		var to: Vector2 = move["to"]
		await _tween_camera_to((from + to) / 2.0, AI_CAMERA_ZOOM, AI_CAMERA_PAN_SECONDS)
		await get_tree().create_timer(ArmyLayer.MOVE_SECONDS).timeout

	await _tween_camera_to(start_focus, start_zoom, AI_CAMERA_PAN_SECONDS)


## Eases _cam_focus/_cam_zoom to the given values over `duration` seconds,
## re-deriving the world transform and re-projecting markers every step so the
## pan/zoom is actually visible rather than just a value change. Awaiting the
## returned value blocks until the tween completes.
func _tween_camera_to(focus: Vector2, zoom: float, duration: float) -> void:
	var tween := create_tween()
	tween.set_trans(Tween.TRANS_SINE).set_ease(Tween.EASE_IN_OUT)
	tween.set_parallel(true)
	tween.tween_method(_set_cam_focus, _cam_focus, focus, duration)
	tween.tween_method(_set_cam_zoom, _cam_zoom, zoom, duration)
	await tween.finished


func _set_cam_focus(focus: Vector2) -> void:
	_cam_focus = focus
	_update_camera_transform()
	_project_markers()


func _set_cam_zoom(zoom: float) -> void:
	_cam_zoom = zoom
	_update_camera_transform()
	_project_markers()


## Refreshes the core HUD (always) and the settlement panel (only while a
## city is selected). The settlement panel - CityPanel + BuildingsPanel,
## built by HudBuilder - only ever shows a city the player explicitly
## clicked; with nothing selected it's hidden entirely rather than falling
## back to some "current" city, so the baseline HUD after closing/deselecting
## is just the persistent top bar/end-turn ribbon with no settlement info
## lingering.
func _refresh_bottom_banner(state: Dictionary) -> void:
	end_turn_button.text = "END TURN %d" % int(state["turn"])
	end_turn_button.disabled = bool(state["game_over"]) or _ai_running

	var shown_city: Dictionary = {}
	if _selected_city_id != -1:
		for city in state["cities"]:
			if int(city["id"]) == _selected_city_id:
				shown_city = city
				break
		# The selected city no longer exists (e.g. captured/eliminated) -
		# clear the stale selection rather than silently showing nothing.
		if shown_city.is_empty():
			_selected_city_id = -1

	if shown_city.is_empty():
		_city_panel.visible = false
		_buildings_panel.visible = false
		return

	_city_panel.visible = true
	_buildings_panel.visible = true

	_city_panel_name_label.text = shown_city["name"]
	_city_panel_owner_tab.color = _faction_colors[int(shown_city["owner"]) % _faction_colors.size()]

	var income: int = int(shown_city["income"])
	for row_def in STAT_ROWS:
		var key: String = row_def[0]
		var multiplier: int = row_def[3]
		_city_stat_value_labels[key].text = HudBuilder.format_stat(income * multiplier)


## Top-middle turn-order indicator: shows whichever faction is acting this
## turn (state["current_faction"]), so it updates both on a new turn and on
## every faction-to-faction handoff within _run_ai_factions()'s sequence, not
## just once per turn number.
func _refresh_turn_indicator(state: Dictionary) -> void:
	if _turn_indicator_name_label == null:
		return
	var current_faction: int = state["current_faction"]
	var faction_name := "Faction %d" % current_faction
	for faction in state["factions"]:
		if int(faction["id"]) == current_faction:
			faction_name = faction["name"]
			break
	_turn_indicator_name_label.text = faction_name
