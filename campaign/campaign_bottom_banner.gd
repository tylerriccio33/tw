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


## Rebuilds the Buildings tray for `city` from the real Rust catalog/state:
## a progress card if `city` has a construction under way, otherwise one
## clickable card per building `manager.available_buildings()` still allows,
## each labelled with its cost/turns/population requirement and disabled if
## the current faction can't afford it or doesn't meet the population gate.
static func refresh_buildings_cards(ui: Node, city: Dictionary) -> void:
	for child in ui._buildings_cards_hbox.get_children():
		child.queue_free()

	var city_id: int = int(city["id"])
	var population: int = int(city.get("population", 0))
	var money := 0
	for faction in ui.manager.get_state().get("factions", []):
		if int(faction["id"]) == int(city["owner"]):
			money = int(faction["money"])
			break

	var construction: Variant = city.get("construction")
	if construction != null:
		ui._buildings_cards_hbox.add_child(
			HudBuildingsBuilder.build_construction_progress_card(construction)
		)
		return

	var available: Array = ui.manager.available_buildings(city_id)
	if available.is_empty():
		ui._buildings_cards_hbox.add_child(HudBuildingsBuilder.build_no_buildings_card())
		return

	for building in available:
		var can_afford: bool = money >= int(building["gold_cost"])
		var enough_population: bool = population >= int(building["required_population"])
		var card := HudBuildingsBuilder.build_available_building_card(
			building, can_afford and enough_population
		)
		card.pressed.connect(ui._on_build_building_pressed.bind(city_id, String(building["key"])))
		ui._buildings_cards_hbox.add_child(card)


## Buildings-tray card pressed: asks Rust to start construction, then
## re-syncs the whole HUD off a fresh get_state() (the treasury debit and new
## construction entry are both reflected there immediately).
static func on_build_building_pressed(ui: Node, city_id: int, building_key: String) -> void:
	if ui.manager.start_construction(city_id, building_key):
		ui._append_log("Construction of %s started." % building_key.capitalize())
		ui._refresh()
