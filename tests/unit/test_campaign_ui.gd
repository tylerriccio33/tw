extends GutTest
## Covers the pure geometry helpers in campaign/campaign_ui.gd. The rest of the
## script wires up a live scene (manager, world layer, HUD) and is exercised
## visually via `make shot` / campaign_smoke.gd instead.

const CampaignUI := preload("res://campaign/campaign_ui.gd")
const MapPackage := preload("res://campaign/map_package.gd")
const ProvinceMapStub := preload("res://tests/fixtures/province_map_stub.gd")
const CampaignScene := preload("res://campaign/campaign.tscn")

var ui: Node2D


func before_each() -> void:
	ui = CampaignUI.new()


func after_each() -> void:
	ui.free()


func test_world_space_province_table_converts_centroid() -> void:
	ui._province_map = _stub_province_map([_province(1, [10, 20], [10, 20])])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_eq(rows[0]["centroid"], [10.0 * 20.0 - 100.0, 20.0 * 20.0 - 100.0])


## The bug this guards: city_position used to be copied verbatim from the map
## package's map-pixel space straight into the table Rust reads, while only
## centroid was converted. Rust then sited every faction's starting army on
## its raw, tiny map-pixel city_position instead of the real world-space
## point - so every army spawned stacked on top of each other near the map's
## centre instead of at their own capitals.
func test_world_space_province_table_converts_city_position_independently_of_centroid() -> void:
	ui._province_map = _stub_province_map([_province(1, [10, 20], [50, 60])])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_eq(rows[0]["city_position"], [50.0 * 20.0 - 100.0, 60.0 * 20.0 - 100.0])
	assert_ne(
		rows[0]["city_position"],
		rows[0]["centroid"],
		"city_position must convert on its own point, not fall back to centroid's"
	)


func test_world_space_province_table_leaves_city_position_out_when_package_has_none() -> void:
	var province := {"id": 1, "centroid": [10, 20]}
	ui._province_map = _stub_province_map([province])
	var rows: Array = ui.call("_world_space_province_table", Vector2(100, 100))
	assert_false(rows[0].has("city_position"))


func _province(id: int, centroid: Array, city_position: Array) -> Dictionary:
	return {"id": id, "centroid": centroid, "city_position": city_position}


func _stub_province_map(provinces: Array) -> Node:
	var package := MapPackage.new()
	package.provinces = provinces
	var stub := ProvinceMapStub.new()
	stub.package = package
	add_child_autofree(stub)
	return stub


## ---------------------------------------------------------------------------
## _move_target_for_province: resolves a clicked province to a world-space
## move target, whether or not a city stands in it. Regression coverage for
## the bug where clicking a neighboring province with no city silently issued
## no order at all, since resolution used to only ever match city provinces.
## ---------------------------------------------------------------------------


func test_move_target_for_province_resolves_city_centroid_and_missing_cases() -> void:
	ui._city_world_positions = {5: Vector2(123.0, 456.0)}
	var city_state := {"cities": [{"id": 5, "province": 2}]}
	var city_target: Vector2 = ui.call("_move_target_for_province", 2, city_state)
	assert_eq(city_target, Vector2(123.0, 456.0))

	ui._province_map = _stub_province_map([])
	ui._province_map.province_centers = {7: Vector2(10.0, 20.0)}
	ui._province_map.map_size = Vector2(100.0, 100.0)
	var empty_state := {"cities": []}
	var centroid_target: Vector2 = ui.call("_move_target_for_province", 7, empty_state)
	var half_size := Vector2(100.0, 100.0) * CampaignUI.MAP_SCALE / 2.0
	assert_eq(centroid_target, Vector2(10.0, 20.0) * CampaignUI.MAP_SCALE - half_size)

	var missing_target: Vector2 = ui.call("_move_target_for_province", 99, empty_state)
	assert_eq(missing_target, Vector2.INF)


## ---------------------------------------------------------------------------
## Zoom direction: pixels_per_unit is _base_ppu / _cam_zoom, so _cam_zoom is an
## *inverse* zoom factor - raising it shrinks pixels_per_unit. _base_ppu is a
## cover fit at _cam_zoom == 1.0, so 1.0 has to be the *ceiling* on _cam_zoom,
## not the floor: MIN_ZOOM/MAX_ZOOM used to be (1.0, 2.2), which let X (and
## scroll-down) push _cam_zoom past 1.0 and shrink pixels_per_unit below the
## cover-fit ratio - exactly the "zoom out with the X key and see grey" bug,
## distinct from (and not caught by) the focus-clamp tests above, which never
## exercised a _cam_zoom above 1.0.
## ---------------------------------------------------------------------------


func test_max_zoom_does_not_exceed_the_cover_fit_ratio() -> void:
	assert_lte(
		CampaignUI.MAX_ZOOM,
		1.0,
		"MAX_ZOOM above 1.0 shrinks pixels_per_unit below the cover-fit ratio and exposes background"
	)


func test_min_zoom_is_stricter_than_max_zoom() -> void:
	assert_lt(CampaignUI.MIN_ZOOM, CampaignUI.MAX_ZOOM)


## ---------------------------------------------------------------------------
## Camera-focus clamp: keeps the map's edge from falling short of the
## viewport's edge, which is what let the camera zoom out and reveal the
## grey background past the map.
## ---------------------------------------------------------------------------


func test_clamp_cam_focus_leaves_a_centred_focus_alone_at_cover_fit_zoom() -> void:
	# At MIN_ZOOM (1.0), pixels_per_unit is exactly the cover-fit ratio, so the
	# map exactly fills the viewport when centred - no room to pan at all.
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 0.8  # viewport.x / (2 * half_size.x) == viewport.y / (2 * half_size.y)
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(Vector2.ZERO, half_size, viewport, ppu)
	assert_eq(clamped, Vector2.ZERO)


func test_clamp_cam_focus_pulls_an_off_map_focus_back_to_the_map_edge() -> void:
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 0.8
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(
		Vector2(5000, 5000), half_size, viewport, ppu
	)
	# half_size - half_viewport_world = (1000, 500) - (500, 250)
	assert_eq(clamped, Vector2(500, 250))


func test_clamp_cam_focus_allows_panning_once_zoomed_in() -> void:
	# Doubling pixels_per_unit halves how much world the viewport covers, so
	# there is now room to pan toward an edge without exposing the background.
	var half_size := Vector2(1000, 500)
	var viewport := Vector2(800, 400)
	var ppu := 1.6
	var clamped: Vector2 = CampaignUI._clamp_cam_focus(
		Vector2(5000, 5000), half_size, viewport, ppu
	)
	# half_size - half_viewport_world = (1000, 500) - (250, 125)
	assert_eq(clamped, Vector2(750, 375))


## ---------------------------------------------------------------------------
## Top bar / bottom banner rendering: unlike the tests above, these load the
## real campaign.tscn (rather than a bare CampaignUI.new()) so _ready() runs
## end to end - the sizing bug below only shows up once UI/BottomBanner/
## TopBar are real scene children with anchors, which a bare script instance
## doesn't have.
## ---------------------------------------------------------------------------


## Regression test for a real bug: UI/BottomBanner/TopBar are full-rect
## anchored Controls parented directly under a Node2D, not another Control.
## Godot only recomputes an anchored Control's size on a viewport *resize*
## notification, and the viewport is already at its final size before the
## scene's first frame runs (content_scale_size is set synchronously in
## _ready), so that notification never fires - every one of them was stuck
## at size (0, 0) forever, and the whole HUD rendered shrunk into a sliver
## pinned at the top-left instead of covering the screen. _sync_ui_root_size()
## in campaign_ui.gd primes their size explicitly to guard against this.
func test_hud_root_controls_fill_the_viewport_after_ready() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var viewport_size: Vector2 = instance.get_viewport_rect().size
	var bottom_banner: Control = instance.get_node("UI/BottomBanner")
	var top_bar: Control = instance.get_node("UI/TopBar")

	assert_gt(bottom_banner.size.x, 0.0, "bottom banner width is stuck at zero")
	assert_gt(bottom_banner.size.y, 0.0, "bottom banner height is stuck at zero")
	assert_almost_eq(bottom_banner.size.x, viewport_size.x, 1.0)
	assert_almost_eq(bottom_banner.size.y, viewport_size.y, 1.0)
	assert_gt(top_bar.size.x, 0.0, "top bar width is stuck at zero")
	assert_almost_eq(top_bar.size.x, viewport_size.x, 1.0)


## The bottom banner (city panel/buildings tray/end-turn button) must
## actually render in the bottom portion of the screen - it used to collapse
## into the top-left corner because of the zero-size bug above.
func test_bottom_banner_widgets_render_in_the_bottom_half_of_the_screen() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var viewport_size: Vector2 = instance.get_viewport_rect().size
	var end_turn_button: Button = instance.end_turn_button
	var city_panel: Control = instance.get_node("UI/BottomBanner/CityPanel")

	assert_gt(
		end_turn_button.position.y,
		viewport_size.y / 2.0,
		"end turn button should sit low on screen"
	)
	assert_gt(city_panel.position.y, viewport_size.y / 2.0, "city panel should sit low on screen")


## The new Attila-style resource strip: treasury/deficit/food/season/year on
## the left, settlements/armies/wiki buttons on the right. Render-only - no
## behavior wired up yet.
func test_top_bar_has_resource_stats_and_nav_buttons() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var stat_labels: Dictionary = instance._top_bar_stat_value_labels
	for expected_stat in ["Treasury", "Deficit", "Food", "Season", "Year"]:
		assert_true(stat_labels.has(expected_stat), "missing top bar stat: %s" % expected_stat)

	assert_not_null(instance.settlements_button)
	assert_not_null(instance.armies_button)
	assert_not_null(instance.wiki_button)
	assert_not_null(instance.log_button)

	var top_bar: Control = instance.get_node("UI/TopBar")
	var viewport_size: Vector2 = instance.get_viewport_rect().size
	assert_lt(
		top_bar.get_node("Bar").size.y,
		viewport_size.y / 4.0,
		"top bar should be a thin strip, not a large chunk of the screen"
	)
	# Nav buttons belong on the right side of the bar, past its horizontal midpoint.
	assert_gt(instance.wiki_button.global_position.x, viewport_size.x / 2.0)
	assert_gt(instance.log_button.global_position.x, viewport_size.x / 2.0)


## The game log used to be an always-visible RichTextLabel floating over the
## map. It now lives behind a top-bar icon: hidden until clicked, and toggled
## shut again on a second click.
func test_log_button_toggles_the_log_panel() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	assert_false(instance._log_panel.visible, "log panel should start hidden")

	instance.log_button.pressed.emit()
	assert_true(instance._log_panel.visible, "log panel should show after clicking the log button")

	instance.log_button.pressed.emit()
	assert_false(instance._log_panel.visible, "log panel should hide again on a second click")


## Turn/battle messages append into the log panel's RichTextLabel, not the old
## always-visible LogLabel node (removed from campaign.tscn).
func test_append_log_writes_into_the_log_panel_label() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	instance.call("_append_log", "Turn 1: faction 0 begins.")
	assert_true(instance.log_label.get_parsed_text().contains("Turn 1: faction 0 begins."))


## ---------------------------------------------------------------------------
## Turn indicator: a small top-middle widget (Total War-style) showing an
## empty-circle placeholder plus the acting faction's name below it. Rewritten
## every _refresh() off state["current_faction"], so it must track both a
## brand new turn and each faction-to-faction handoff within one turn.
## ---------------------------------------------------------------------------


static func _faction_name(instance: Node2D, faction_id: int) -> String:
	for faction in instance.manager.get_state()["factions"]:
		if int(faction["id"]) == faction_id:
			return faction["name"]
	return ""


## After the scene loads, the indicator should already reflect whichever
## faction is acting first (state["current_faction"]), not a placeholder.
func test_turn_indicator_shows_the_starting_factions_name() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var state: Dictionary = instance.manager.get_state()
	var expected_name: String = _faction_name(instance, int(state["current_faction"]))

	assert_not_null(instance._turn_indicator_name_label)
	assert_eq(instance._turn_indicator_name_label.text, expected_name)


## Ending the turn advances state["current_faction"] in the Rust sim; the
## indicator's label must be rewritten to the new faction's name rather than
## staying stuck on whoever went first.
func test_turn_indicator_updates_when_the_active_faction_advances() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var starting_name: String = instance._turn_indicator_name_label.text

	instance.manager.end_turn()
	instance.call("_refresh")

	var state: Dictionary = instance.manager.get_state()
	var expected_name: String = _faction_name(instance, int(state["current_faction"]))

	assert_eq(instance._turn_indicator_name_label.text, expected_name)
	assert_ne(
		instance._turn_indicator_name_label.text,
		starting_name,
		"indicator should move on from the first faction once the turn advances"
	)


## Multi-faction sequence: end_turn() cycles current_faction round the whole
## roster (see rust/campaign/src/model.rs advance_turn), so the indicator has
## to keep matching state["current_faction"] across several consecutive
## advances, not just the first one.
func test_turn_indicator_tracks_multiple_consecutive_turn_advances() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	for _i in range(4):
		instance.manager.end_turn()
		instance.call("_refresh")
		var state: Dictionary = instance.manager.get_state()
		if bool(state["game_over"]):
			break
		var expected_name: String = _faction_name(instance, int(state["current_faction"]))
		assert_eq(instance._turn_indicator_name_label.text, expected_name)


## ---------------------------------------------------------------------------
## Region clicks: on the real scene (not a bare CampaignUI.new()), since
## _on_region_clicked reads from the real manager/_army_layer that _ready()
## wires up - a click has to behave differently depending on whether an army
## is currently selected.
## ---------------------------------------------------------------------------


func _player_army_id(instance: Node2D) -> int:
	for army in instance.manager.get_state().get("armies", []):
		if int(army["owner"]) == 0:
			return int(army["id"])
	return -1


## No army selected: a province click falls back to the old behavior of
## selecting whichever city stands in it, exactly like clicking its marker.
func test_region_clicked_selects_the_citys_province_when_no_army_is_selected() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var city: Dictionary = instance.manager.get_state()["cities"][0]
	instance.call("_on_region_clicked", int(city["province"]))

	assert_eq(instance._selected_city_id, int(city["id"]))


## With an army selected, a click on a reachable province's land is a move
## order onto that province's city - the whole point of the highlight this
## covers: there's no marker to click directly on a bordering province that
## has no army/city marker sitting under the mouse.
func test_region_clicked_orders_the_selected_army_onto_the_clicked_provinces_city() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var army_id := _player_army_id(instance)
	instance._army_layer.select(army_id)
	var reachable: Array = instance.manager.reachable_provinces(army_id)
	assert_false(reachable.is_empty(), "test needs an army with somewhere to go")

	# Pick a reachable province whose city is friendly (same owner as the
	# moving army) rather than just reachable[0]: marching onto an enemy
	# city triggers a randomized siege via resolve_arrival, which can kill
	# or repel the army and make the landing position nondeterministic.
	# This test only cares that an uncontested move lands exactly on the
	# clicked province's city.
	var army_owner: int = 0
	var army_province: int = -1
	for army in instance.manager.get_state()["armies"]:
		if int(army["id"]) == army_id:
			army_owner = int(army["owner"])
			for city in instance.manager.get_state()["cities"]:
				if (
					abs(float(city["x"]) - float(army["x"])) < 0.5
					and abs(float(city["y"]) - float(army["y"])) < 0.5
				):
					army_province = int(city.get("province", -1))
					break
			break

	# Only a directly-adjacent province is a valid single-hop move order
	# (move_army_by_province rejects anything reachable_provinces' multi-hop
	# BFS surfaces beyond one border), so restrict candidates to the army's
	# current province's declared neighbors rather than the full reachable set.
	var home_neighbors: Array = (
		instance._province_map.package.province_by_id.get(army_province, {}).get("neighbors", [])
	)

	var target_province: int = -1
	var target_city: Dictionary
	for city in instance.manager.get_state()["cities"]:
		var province: int = int(city.get("province", -1))
		if (
			home_neighbors.has(province)
			and reachable.has(province)
			and int(city.get("owner", -1)) == army_owner
		):
			target_province = province
			target_city = city
			break
	if target_province == -1:
		# TODO: this map's starting layout leaves every army bordered only by
		# enemy territory, so there's no reachable friendly city to move onto
		# uncontested. Once starting positions/ownership give some army a
		# friendly neighbor again, make this deterministic instead of
		# skipping - possibly by asserting on the siege outcome directly.
		pending("no reachable, friendly-owned city to move onto uncontested")
		return

	instance.call("_on_region_clicked", target_province)

	var after: Dictionary
	for army in instance.manager.get_state()["armies"]:
		if int(army["id"]) == army_id:
			after = army
			break
	assert_almost_eq(float(after["x"]), float(target_city["x"]), 0.5)
	assert_almost_eq(float(after["y"]), float(target_city["y"]), 0.5)


## A click on a province with no city standing in it (unowned/cityless) must
## not crash and must not select anything, army-selected or not - there is
## nothing in campaign_ui's `state["cities"]` for it to match.
func test_region_clicked_on_a_cityless_province_does_nothing() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	instance.call("_on_region_clicked", -999)

	assert_eq(instance._selected_city_id, -1)


## ---------------------------------------------------------------------------
## Settlement panel show/hide: the city panel + buildings tray are the
## selection-dependent half of the bottom banner (see _refresh_bottom_banner
## in campaign_ui.gd). They must start hidden, appear only once a settlement
## is actually clicked, and disappear again - along with clearing the
## selection - when the panel's back/close button is pressed, leaving only
## the persistent core HUD (top bar, end-turn button) on screen.
## ---------------------------------------------------------------------------


func test_settlement_panel_appears_on_marker_click() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	assert_eq(instance._selected_city_id, -1)
	assert_false(instance._city_panel.visible, "city panel should start hidden")
	assert_false(instance._buildings_panel.visible, "buildings panel should start hidden")
	# Core HUD stays up regardless of selection state.
	assert_true(instance.end_turn_button.visible)
	assert_true(instance.get_node("UI/TopBar").visible)

	var city: Dictionary = instance.manager.get_state()["cities"][0]
	instance.call("_on_city_marker_clicked", int(city["id"]))

	assert_eq(instance._selected_city_id, int(city["id"]))
	assert_true(instance._city_panel.visible, "city panel should show after selecting a settlement")
	assert_true(
		instance._buildings_panel.visible,
		"buildings panel should show after selecting a settlement"
	)
	assert_eq(instance._city_panel_name_label.text, String(city["name"]))


func test_settlement_panel_closes_and_clears_selection_on_back_button() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var city: Dictionary = instance.manager.get_state()["cities"][0]
	instance.call("_on_city_marker_clicked", int(city["id"]))
	assert_true(instance._city_panel.visible)

	instance.call("_on_settlement_panel_close_pressed")

	assert_eq(instance._selected_city_id, -1, "closing the panel must clear the selection")
	assert_false(instance._city_panel.visible, "city panel should hide after closing")
	assert_false(instance._buildings_panel.visible, "buildings panel should hide after closing")
	# Core HUD is untouched by closing the settlement panel.
	assert_true(instance.end_turn_button.visible)
	assert_true(instance.get_node("UI/TopBar").visible)


func test_settlement_panel_close_button_is_wired_to_the_close_handler() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var city: Dictionary = instance.manager.get_state()["cities"][0]
	instance.call("_on_city_marker_clicked", int(city["id"]))
	assert_true(instance._city_panel.visible)

	var close_button: Button = instance.get_node("UI/BottomBanner/CityPanel").find_child(
		"CloseButton", true, false
	)
	assert_not_null(close_button, "settlement panel should have a close/back button")
	close_button.pressed.emit()

	assert_eq(instance._selected_city_id, -1)
	assert_false(instance._city_panel.visible)


## ---------------------------------------------------------------------------
## AI-turn camera: while an AI faction's turn resolves, the camera should pan/
## zoom onto each army_moved signal it produces (Total War-style "watch the
## enemy turn"), the same way a human player's own moves never should - see
## the _watching_ai_moves comment in campaign_ui.gd for why a flag, not a
## faction check, is what tells the two apart.
## ---------------------------------------------------------------------------


## The army_moved signal fires for player moves too (order_selected_to routes
## through the same manager.move_army/army_moved path) - it must only be
## collected as an "AI move to focus the camera on" while
## _watching_ai_moves is set, or the player's own orders would yank their
## camera around.
func test_army_moved_is_ignored_for_the_camera_when_not_watching_ai_moves() -> void:
	ui._watching_ai_moves = false
	ui.call("_on_army_moved_for_camera", 1, Vector2(0, 0), Vector2(100, 50), 5.0, 0.0)
	assert_true(ui._ai_moves.is_empty(), "a move outside an AI turn must not queue a camera pan")


func test_army_moved_is_queued_for_the_camera_while_watching_ai_moves() -> void:
	ui._watching_ai_moves = true
	ui.call("_on_army_moved_for_camera", 7, Vector2(10, 20), Vector2(110, 220), 5.0, 0.0)
	assert_eq(ui._ai_moves.size(), 1)
	assert_eq(ui._ai_moves[0]["from"], Vector2(10, 20))
	assert_eq(ui._ai_moves[0]["to"], Vector2(110, 220))


## run_ai_turn() plays every army the faction owns in one call, so several
## army_moved signals can land before control returns - all of them must be
## collected, not just the last.
func test_multiple_army_moved_signals_all_queue_while_watching() -> void:
	ui._watching_ai_moves = true
	ui.call("_on_army_moved_for_camera", 1, Vector2.ZERO, Vector2(50, 0), 5.0, 0.0)
	ui.call("_on_army_moved_for_camera", 2, Vector2.ZERO, Vector2(0, 50), 5.0, 0.0)
	assert_eq(ui._ai_moves.size(), 2)


## _tween_camera_to drives the same _cam_focus/_cam_zoom that
## _update_camera_transform reads, so awaiting it should land the camera
## exactly on the requested focus/zoom - run against the real scene since
## _update_camera_transform touches world_layer, which only exists once
## _ready() has built it.
func test_tween_camera_to_lands_on_the_requested_focus_and_zoom() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	var target := Vector2(123.0, -45.0)
	await instance.call("_tween_camera_to", target, CampaignUI.AI_CAMERA_ZOOM, 0.05)

	assert_almost_eq(instance._cam_focus.x, target.x, 1.0)
	assert_almost_eq(instance._cam_focus.y, target.y, 1.0)
	assert_almost_eq(instance._cam_zoom, CampaignUI.AI_CAMERA_ZOOM, 0.01)


## End-to-end: drive an actual AI turn (faction 1, after the player ends turn
## 0) through run_ai_turn() under the same _watching_ai_moves window
## _run_ai_factions uses, and check the queued camera targets are real army
## positions from the resulting state, not placeholders.
func test_ai_turn_queues_camera_moves_matching_the_armies_new_positions() -> void:
	var instance: Node2D = (CampaignScene as PackedScene).instantiate()
	add_child_autofree(instance)
	await wait_process_frames(3)

	instance.manager.end_turn()
	assert_eq(instance.manager.current_faction_id(), 1, "test needs faction 1's turn to be next")

	instance._ai_moves.clear()
	instance._watching_ai_moves = true
	instance.manager.run_ai_turn()
	instance._watching_ai_moves = false

	assert_false(instance._ai_moves.is_empty(), "faction 1's armies should have moved")

	# Every queued move must be a real displacement (never a zero-length
	# "move" queued for the camera to chase), and its `to` must fall within
	# the map bounds run_ai_turn's own movement clamp guarantees.
	for move in instance._ai_moves:
		var from: Vector2 = move["from"]
		var to: Vector2 = move["to"]
		assert_ne(from, to, "a queued camera move should reflect actual army movement")
		assert_true(
			absf(to.x) <= instance._map_extent + 0.01 and absf(to.y) <= instance._map_extent + 0.01,
			"queued camera move target should stay on the map"
		)
