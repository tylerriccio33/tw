extends RefCounted
## Bottom-banner (city/army/province info panel + buildings tray) refresh
## logic, split out of campaign_ui.gd to keep it under the gdlint file line
## limit. These read/write directly onto the campaign_ui instance passed in
## as `ui`, the same convention campaign_hud_builder.gd uses for widget
## construction.

const HudBuilder := preload("res://campaign/campaign_hud_builder.gd")
const HudBuildingsBuilder := preload("res://campaign/campaign_hud_buildings_builder.gd")


## Refreshes the core HUD (always) and the settlement panel (only while a city
## is selected - hidden entirely otherwise, never falling back to a "current" city).
static func refresh_bottom_banner(ui: Node, state: Dictionary) -> void:
	ui.end_turn_button.text = "END TURN %d" % int(state["round"])
	ui.end_turn_button.disabled = (
		bool(state["game_over"])
		or ui._ai_running
		or ui._player_defeated
		or ui._battle_ui.is_pending()
	)

	var year_label: Label = ui._top_bar_stat_value_labels.get("Year")
	if year_label != null:
		var year: int = ui.START_YEAR + (int(state["round"]) - 1) * ui.YEARS_PER_TURN
		year_label.text = "%d AD" % year

	var shown_city: Dictionary = {}
	if ui._selected_city_id != -1:
		for city in state["cities"]:
			if int(city["id"]) == ui._selected_city_id:
				shown_city = city
				break
		# The selected city no longer exists (e.g. captured/eliminated) -
		# clear the stale selection rather than silently showing nothing.
		if shown_city.is_empty():
			ui._selected_city_id = -1

	if not shown_city.is_empty():
		ui._city_panel.visible = true
		ui._buildings_panel.visible = true

		ui._city_panel_name_label.text = shown_city["name"]
		ui._city_panel_owner_tab.color = (ui._faction_colors[
			int(shown_city["owner"]) % ui._faction_colors.size()
		])

		var income: int = int(shown_city["income"])
		for row_def in ui.STAT_ROWS:
			var key: String = row_def[0]
			if key == "population":
				# Real per-city value from Rust (grows 5%/turn there), not a
				# placeholder derived from income like the rows below it.
				ui._city_stat_value_labels[key].text = (HudBuilder.format_stat(
					int(shown_city.get("population", 0))
				))
				continue
			var multiplier: int = row_def[3]
			ui._city_stat_value_labels[key].text = HudBuilder.format_stat(income * multiplier)

		# Only meaningful for a settlement the player's own faction holds -
		# an enemy city can't be queued for construction, so just leave the
		# tray showing whatever it already showed rather than building cards
		# for a city the player can't act on.
		if int(shown_city["owner"]) == int(state["current_faction"]):
			refresh_buildings_cards(ui, shown_city)
		return

	# An army marker was clicked: show its owner/movement/position in the same
	# panel. Reuses the settlement panel's name label + owner tab + stat rows
	# rather than adding new widgets, since there's no dedicated army panel;
	# the stat rows don't semantically match army fields, so their labels are
	# just repurposed to carry movement/position text.
	var shown_army: Dictionary = {}
	if ui._selected_army_id != -1:
		for army in state.get("armies", []):
			if int(army["id"]) == ui._selected_army_id:
				shown_army = army
				break
		if shown_army.is_empty():
			ui._selected_army_id = -1
			ui._army_layer.select(-1)

	if not shown_army.is_empty():
		ui._city_panel.visible = true
		ui._buildings_panel.visible = false

		var owner_id: int = int(shown_army["owner"])
		var faction_name := "Faction %d" % owner_id
		for faction in state.get("factions", []):
			if int(faction["id"]) == owner_id:
				faction_name = String(faction["name"])
				break

		ui._city_panel_name_label.text = "%s (%s)" % [shown_army["name"], faction_name]
		ui._city_panel_owner_tab.color = ui._faction_colors[owner_id % ui._faction_colors.size()]

		for row_def in ui.STAT_ROWS:
			ui._city_stat_value_labels[row_def[0]].text = "-"
		ui._city_stat_value_labels["income"].text = (
			"%.1f / %.1f" % [float(shown_army["movement"]), float(shown_army["max_movement"])]
		)
		ui._city_stat_value_labels["food"].text = (
			"(%.0f, %.0f)" % [float(shown_army["x"]), float(shown_army["y"])]
		)
		ui._city_stat_value_labels["region_wealth"].text = (
			"Garrisoned" if int(shown_army["garrisoned"]) != -1 else "In the field"
		)
		return

	# A province with no city of its own was clicked: show its name/owner in
	# the same panel rather than nothing, but there's no settlement to run a
	# buildings/military tray for.
	var shown_province: Dictionary = {}
	if ui._selected_province_id != -1:
		for province in state.get("provinces", []):
			if int(province["id"]) == ui._selected_province_id:
				shown_province = province
				break
		if shown_province.is_empty():
			ui._selected_province_id = -1

	if shown_province.is_empty():
		ui._city_panel.visible = false
		ui._buildings_panel.visible = false
		return

	ui._city_panel.visible = true
	ui._buildings_panel.visible = false

	ui._city_panel_name_label.text = shown_province["name"]
	var owner: int = int(shown_province["owner"])
	ui._city_panel_owner_tab.color = (
		ui._faction_colors[owner % ui._faction_colors.size()]
		if owner >= 0
		else Color(0.5, 0.5, 0.5)
	)

	for row_def in ui.STAT_ROWS:
		var key: String = row_def[0]
		ui._city_stat_value_labels[key].text = "-"


## Rebuilds the Buildings tray for `city`: one card per building the city
## already has (city["buildings"], a plain array of building keys), plus a
## progress card first if a project is currently under way. Buildable-but-
## not-yet-built entries no longer show here - see the Recruitment/
## Construction modal (populate_recruitment_modal below) for those.
static func refresh_buildings_cards(ui: Node, city: Dictionary) -> void:
	for child in ui._buildings_cards_hbox.get_children():
		child.queue_free()

	var construction: Variant = city.get("construction")
	if construction != null:
		ui._buildings_cards_hbox.add_child(
			HudBuildingsBuilder.build_construction_progress_card(construction)
		)

	var built: Array = city.get("buildings", [])
	if built.is_empty() and construction == null:
		ui._buildings_cards_hbox.add_child(HudBuildingsBuilder.build_no_buildings_card())
		return

	for key in built:
		ui._buildings_cards_hbox.add_child(
			HudBuildingsBuilder.build_owned_building_card(String(key))
		)


## Buildings-tray card pressed: asks Rust to start construction, then
## re-syncs the whole HUD off a fresh get_state() (the treasury debit and new
## construction entry are both reflected there immediately).
static func on_build_building_pressed(ui: Node, city_id: int, building_key: String) -> void:
	if ui.manager.start_construction(city_id, building_key):
		ui._append_log("Construction of %s started." % building_key.capitalize())
		ui._refresh()
		# The modal's own buildable list is stale the instant this succeeds
		# (the building just started is no longer available) - repopulate it
		# in place rather than closing it, so the player can queue more than
		# one city's worth of construction without reopening the modal.
		if ui._recruitment_modal.visible:
			populate_recruitment_modal(ui)


## Toggles the Recruitment/Construction modal for whichever city is currently
## selected - bound to the city panel's ⚒ button (RecruitmentButton).
## Repopulates on every open so it always reflects live state (gold changes,
## a construction just started elsewhere, etc).
static func toggle_recruitment_modal(ui: Node) -> void:
	if ui._selected_city_id == -1:
		return
	ui._recruitment_modal.visible = not ui._recruitment_modal.visible
	if ui._recruitment_modal.visible:
		populate_recruitment_modal(ui)


## Rebuilds both columns of the Recruitment/Construction modal for the
## currently selected city: buildable buildings (manager.available_buildings)
## with a Build button per row, and the stub Army/Garrison troop rows (same
## display-only lists the Military tab already uses - see ui.ARMY_UNITS/
## ui.GARRISON_UNITS).
static func populate_recruitment_modal(ui: Node) -> void:
	var build_list: VBoxContainer = ui._recruitment_buildable_list
	for child in build_list.get_children():
		child.queue_free()

	var city_id: int = ui._selected_city_id
	var state: Dictionary = ui.manager.get_state()
	var city: Dictionary = {}
	for c in state.get("cities", []):
		if int(c["id"]) == city_id:
			city = c
			break

	var population: int = int(city.get("population", 0))
	var money := 0
	for faction in state.get("factions", []):
		if int(faction["id"]) == int(city.get("owner", -1)):
			money = int(faction["money"])
			break

	if city.get("construction") != null:
		build_list.add_child(
			HudBuildingsBuilder.build_construction_progress_card(city["construction"])
		)
	else:
		var available: Array = ui.manager.available_buildings(city_id)
		if available.is_empty():
			build_list.add_child(HudBuildingsBuilder.build_no_buildings_card())
		for building in available:
			var can_afford: bool = money >= int(building["gold_cost"])
			var enough_population: bool = population >= int(building["required_population"])
			var key: String = String(building["key"])
			build_list.add_child(
				HudBuildingsBuilder.build_recruitment_buildable_row(
					building,
					can_afford and enough_population,
					ui._on_build_building_pressed.bind(city_id, key)
				)
			)

	var troop_list: VBoxContainer = ui._recruitment_troops_list
	for child in troop_list.get_children():
		child.queue_free()
	for u in ui.ARMY_UNITS + ui.GARRISON_UNITS:
		troop_list.add_child(
			HudBuildingsBuilder.make_unit_row(
				ui, String(u["name"]), String(u["icon"]), int(u["count"])
			)
		)


## Fires once per building that finished construction during the turn just
## resolved (see rust/campaign/src/model.rs's end_turn). Queues a short-lived
## alert banner rather than showing one immediately, since several buildings
## can complete across multiple factions/cities within the same end_turn()
## call - mirrors battle_ui_controller.gd's queue-and-drain pattern, but
## self-dismisses on a timer instead of waiting on an OK button.
static func on_construction_completed(
	ui: Node, faction_id: int, _city_id: int, city_name: String, building_name: String
) -> void:
	var faction_name := "Faction %d" % faction_id
	for faction in ui.manager.get_state()["factions"]:
		if int(faction["id"]) == faction_id:
			faction_name = String(faction["name"])
			break
	var msg := "%s has constructed %s in %s." % [faction_name, building_name, city_name]
	ui._construction_alert_queue.append(msg)
	if not ui._construction_alert_processing:
		_process_construction_alerts(ui)


## Drains _construction_alert_queue one message at a time, showing each for a
## few seconds before moving to the next (or hiding the modal if the queue is
## now empty).
static func _process_construction_alerts(ui: Node) -> void:
	ui._construction_alert_processing = true
	while not ui._construction_alert_queue.is_empty():
		var msg: String = ui._construction_alert_queue.pop_front()
		ui._construction_alert_label.text = msg
		ui._construction_alert_modal.visible = true
		await ui.get_tree().create_timer(3.0).timeout
	ui._construction_alert_modal.visible = false
	ui._construction_alert_processing = false
