extends RefCounted
## Buildings/Military tab widget construction, split out of
## campaign_hud_builder.gd to keep both files under the gdlint file line
## limit. These are pure node-construction helpers that write results back
## onto the campaign_ui instance passed in as `ui`; shared styling helpers
## (style_box/set_font/anchor_rect) live on campaign_hud_builder.gd.

const LOCAL_FONT_SEMIBOLD := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const LOCAL_FONT_BOLD := preload("res://assets/fonts/Baloo2-Bold.ttf")
const HudBuilder := preload("res://campaign/campaign_hud_builder.gd")


static func build_buildings_panel(ui: Node) -> void:
	var panel := Control.new()
	panel.name = "BuildingsPanel"
	HudBuilder.anchor_rect(panel, 0.19, 0.617, 0.762, 0.99)
	panel.visible = false
	# See the matching comment in _build_city_panel: lift above the army layer.
	panel.z_index = 50
	ui.bottom_banner.add_child(panel)
	ui._buildings_panel = panel

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 0)
	vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	panel.add_child(vbox)

	var tab_group := ButtonGroup.new()
	var tabs_row := HBoxContainer.new()
	tabs_row.add_theme_constant_override("separation", 2)
	tabs_row.custom_minimum_size = Vector2(0, 34)
	vbox.add_child(tabs_row)
	var buildings_tab := _make_tab_button(ui, "Buildings", tab_group, true)
	var military_tab := _make_tab_button(ui, "Military", tab_group, false)
	tabs_row.add_child(buildings_tab)
	tabs_row.add_child(military_tab)

	# Content area: only one of Buildings/Military is visible at a time,
	# toggled by _on_settlement_tab_selected() in campaign_ui.gd.
	var content_area := Control.new()
	content_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	vbox.add_child(content_area)

	ui._buildings_content = _build_buildings_content(ui)
	content_area.add_child(ui._buildings_content)

	ui._military_content = _build_military_content(ui)
	ui._military_content.visible = false
	content_area.add_child(ui._military_content)


static func _build_buildings_content(ui: Node) -> Control:
	var cards_row := PanelContainer.new()
	cards_row.name = "BuildingsContent"
	cards_row.add_theme_stylebox_override("panel", HudBuilder.style_box(ui.HUD_CREAM))
	cards_row.set_anchors_preset(Control.PRESET_FULL_RECT)

	var cards_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		cards_margin.add_theme_constant_override("margin_%s" % side, 4)
	cards_row.add_child(cards_margin)

	var cards_hbox := HBoxContainer.new()
	cards_hbox.add_theme_constant_override("separation", 4)
	cards_margin.add_child(cards_hbox)
	# Populated per-selection by campaign_ui.gd::_refresh_buildings_cards()
	# from the real Rust catalog/construction state - see that function
	# rather than this builder for the actual card contents.
	ui._buildings_cards_hbox = cards_hbox

	var red_strip := PanelContainer.new()
	red_strip.add_theme_stylebox_override("panel", HudBuilder.style_box(ui.HUD_MAROON))
	red_strip.custom_minimum_size = Vector2(56, 0)
	cards_hbox.add_child(red_strip)

	var red_vbox := VBoxContainer.new()
	red_vbox.alignment = BoxContainer.ALIGNMENT_CENTER
	red_vbox.add_theme_constant_override("separation", 8)
	red_vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	red_strip.add_child(red_vbox)
	for glyph in ["🏰", "⛵"]:
		var icon_circle := PanelContainer.new()
		var circle_sb := HudBuilder.style_box(Color(0.15, 0.15, 0.15))
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

	return cards_row


## Military tab content: an "Army" unit list and a "Garrison" unit list, side
## by side, split by a vertical divider - mirrors the two-column layout of
## the buildings tray (cards on the left, red strip on the right) so the tab
## switch doesn't feel like a different HUD. Unit data is entirely stubbed
## (ui.ARMY_UNITS / ui.GARRISON_UNITS in campaign_ui.gd); this only exists to
## get the visuals right.
static func _build_military_content(ui: Node) -> Control:
	var panel := PanelContainer.new()
	panel.name = "MilitaryContent"
	panel.add_theme_stylebox_override("panel", HudBuilder.style_box(ui.HUD_CREAM))
	panel.set_anchors_preset(Control.PRESET_FULL_RECT)

	var margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		margin.add_theme_constant_override("margin_%s" % side, 6)
	panel.add_child(margin)

	var columns := HBoxContainer.new()
	columns.add_theme_constant_override("separation", 0)
	margin.add_child(columns)

	columns.add_child(_make_unit_column(ui, "Army", "⚔", ui.ARMY_UNITS, "No army selected."))

	# Visual divider between the two unit groups - a thin vertical rule with
	# a little breathing room on either side, rather than a bare ColorRect
	# butted up against both columns.
	var divider_wrap := MarginContainer.new()
	divider_wrap.add_theme_constant_override("margin_left", 10)
	divider_wrap.add_theme_constant_override("margin_right", 10)
	var divider := ColorRect.new()
	divider.color = Color(0.6, 0.57, 0.48)
	divider.custom_minimum_size = Vector2(2, 0)
	divider_wrap.add_child(divider)
	columns.add_child(divider_wrap)

	columns.add_child(
		_make_unit_column(ui, "Garrison", "🏰", ui.GARRISON_UNITS, "No garrison stationed.")
	)

	return panel


static func _make_unit_column(
	ui: Node, title: String, title_icon: String, units: Array, empty_text: String
) -> VBoxContainer:
	var col := VBoxContainer.new()
	col.add_theme_constant_override("separation", 6)
	col.size_flags_horizontal = Control.SIZE_EXPAND_FILL

	var header := HBoxContainer.new()
	header.add_theme_constant_override("separation", 6)
	col.add_child(header)

	var icon_label := Label.new()
	icon_label.text = title_icon
	icon_label.add_theme_color_override("font_color", ui.HUD_BLUE)
	header.add_child(icon_label)

	var title_label := Label.new()
	title_label.text = title
	HudBuilder.set_font(title_label, LOCAL_FONT_BOLD, 15)
	title_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	header.add_child(title_label)

	var count_label := Label.new()
	var total := 0
	for u in units:
		total += int(u["count"])
	count_label.text = "%d units" % total if units.size() > 0 else ""
	HudBuilder.set_font(count_label, LOCAL_FONT_SEMIBOLD, 11)
	count_label.add_theme_color_override("font_color", Color(0.45, 0.45, 0.45))
	count_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	count_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	count_label.vertical_alignment = VERTICAL_ALIGNMENT_BOTTOM
	header.add_child(count_label)

	var header_rule := ColorRect.new()
	header_rule.color = Color(0.6, 0.57, 0.48)
	header_rule.custom_minimum_size = Vector2(0, 1)
	col.add_child(header_rule)

	if units.is_empty():
		var empty_label := Label.new()
		empty_label.text = empty_text
		HudBuilder.set_font(empty_label, ui.FONT_MEDIUM, 12)
		empty_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.5))
		col.add_child(empty_label)
		return col

	for u in units:
		col.add_child(_make_unit_row(ui, u["name"], u["icon"], int(u["count"])))

	return col


static func _make_unit_row(ui: Node, unit_name: String, icon: String, count: int) -> PanelContainer:
	var row := PanelContainer.new()
	row.add_theme_stylebox_override("panel", HudBuilder.style_box(Color(1.0, 1.0, 1.0, 0.5)))

	var row_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		row_margin.add_theme_constant_override("margin_%s" % side, 4)
	row.add_child(row_margin)

	var hbox := HBoxContainer.new()
	hbox.add_theme_constant_override("separation", 8)
	row_margin.add_child(hbox)

	var icon_circle := PanelContainer.new()
	var circle_sb := HudBuilder.style_box(ui.HUD_BLUE)
	circle_sb.corner_radius_top_left = 15
	circle_sb.corner_radius_top_right = 15
	circle_sb.corner_radius_bottom_left = 15
	circle_sb.corner_radius_bottom_right = 15
	icon_circle.add_theme_stylebox_override("panel", circle_sb)
	icon_circle.custom_minimum_size = Vector2(30, 30)
	var icon_label := Label.new()
	icon_label.text = icon
	icon_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	icon_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	icon_label.add_theme_color_override("font_color", Color.WHITE)
	icon_circle.add_child(icon_label)
	hbox.add_child(icon_circle)

	var name_label := Label.new()
	name_label.text = unit_name
	HudBuilder.set_font(name_label, LOCAL_FONT_SEMIBOLD, 13)
	name_label.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hbox.add_child(name_label)

	var count_label := Label.new()
	count_label.text = str(count)
	HudBuilder.set_font(count_label, LOCAL_FONT_BOLD, 14)
	count_label.add_theme_color_override("font_color", Color(0.1, 0.1, 0.1))
	count_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	hbox.add_child(count_label)

	return row


static func _make_tab_button(ui: Node, text: String, group: ButtonGroup, active: bool) -> Button:
	var tab := Button.new()
	tab.text = text
	tab.toggle_mode = true
	tab.button_pressed = active
	tab.button_group = group
	tab.focus_mode = Control.FOCUS_NONE
	HudBuilder.set_font(tab, LOCAL_FONT_SEMIBOLD)
	tab.add_theme_color_override("font_color", Color.WHITE)
	tab.add_theme_color_override("font_hover_color", Color.WHITE)
	tab.add_theme_color_override("font_pressed_color", Color.WHITE)
	tab.add_theme_color_override("font_focus_color", Color.WHITE)
	tab.add_theme_constant_override("h_separation", 0)
	var margin_px := 14
	tab.add_theme_stylebox_override(
		"normal", _padded_tab_stylebox(Color(0.11, 0.15, 0.22), margin_px)
	)
	tab.add_theme_stylebox_override(
		"hover", _padded_tab_stylebox(Color(0.18, 0.24, 0.34), margin_px)
	)
	tab.add_theme_stylebox_override(
		"pressed", _padded_tab_stylebox(Color(0.247, 0.353, 0.51), margin_px)
	)
	tab.add_theme_stylebox_override(
		"hover_pressed", _padded_tab_stylebox(Color(0.247, 0.353, 0.51), margin_px)
	)
	tab.pressed.connect(func(): ui._on_settlement_tab_selected(text))
	return tab


static func _padded_tab_stylebox(bg: Color, side_margin: int) -> StyleBoxFlat:
	var sb := HudBuilder.style_box(bg)
	sb.content_margin_left = side_margin
	sb.content_margin_right = side_margin
	return sb


static func _make_building_card(name: String, level: int, locked: bool) -> VBoxContainer:
	var card := VBoxContainer.new()
	card.add_theme_constant_override("separation", 2)
	card.custom_minimum_size = Vector2(84, 0)

	var image_panel := PanelContainer.new()
	image_panel.add_theme_stylebox_override(
		"panel",
		HudBuilder.style_box(Color(0.7, 0.7, 0.7) if locked else Color.WHITE, Color.BLACK, 1)
	)
	image_panel.custom_minimum_size = Vector2(0, 70)
	card.add_child(image_panel)

	if not locked:
		var level_label := Label.new()
		level_label.text = "Lv.%d" % level
		HudBuilder.set_font(level_label, LOCAL_FONT_BOLD, 13)
		level_label.add_theme_color_override("font_color", Color.BLACK)
		image_panel.add_child(level_label)
	elif name == "Mine":
		var q_label := Label.new()
		q_label.text = "?"
		HudBuilder.set_font(q_label, LOCAL_FONT_BOLD, 28)
		q_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		q_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		q_label.add_theme_color_override("font_color", Color(0.1, 0.1, 0.1))
		image_panel.add_child(q_label)

	var caption := Label.new()
	caption.text = name
	HudBuilder.set_font(caption, LOCAL_FONT_SEMIBOLD)
	caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	caption.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	card.add_child(caption)

	return card


## A clickable buildings-tray card for one entry from
## CampaignManager.available_buildings() - `building` is
## `{key, name, gold_cost, build_time_turns, required_population,
## production_resource, production_amount}`. Disabled (greyed, unclickable)
## when the caller determines the current faction can't build it right now
## (not enough gold or population) - campaign_ui.gd::_refresh_buildings_cards
## makes that call, this only renders the result.
static func build_available_building_card(building: Dictionary, enabled: bool) -> Button:
	var card := Button.new()
	card.custom_minimum_size = Vector2(84, 96)
	card.disabled = not enabled
	card.toggle_mode = false

	var production_line := ""
	var resource: String = String(building.get("production_resource", ""))
	if resource != "":
		production_line = "\n+%d %s" % [int(building["production_amount"]), resource.capitalize()]

	card.text = (
		"%s\n%dg / %d turn%s\nPop %d%s"
		% [
			String(building["name"]),
			int(building["gold_cost"]),
			int(building["build_time_turns"]),
			"" if int(building["build_time_turns"]) == 1 else "s",
			int(building["required_population"]),
			production_line,
		]
	)
	HudBuilder.set_font(card, LOCAL_FONT_SEMIBOLD, 11)
	return card


## Shown in the buildings tray instead of the buildable list while a city has
## a project under way - `construction` is `{key, name, turns_remaining}`
## from CampaignManager.get_state()'s per-city "construction" field.
static func build_construction_progress_card(construction: Dictionary) -> VBoxContainer:
	var card := VBoxContainer.new()
	card.custom_minimum_size = Vector2(160, 0)
	card.alignment = BoxContainer.ALIGNMENT_CENTER

	var title := Label.new()
	title.text = "Building: %s" % String(construction["name"])
	HudBuilder.set_font(title, LOCAL_FONT_BOLD, 14)
	title.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	card.add_child(title)

	var turns: int = int(construction["turns_remaining"])
	var subtitle := Label.new()
	subtitle.text = "%d turn%s remaining" % [turns, "" if turns == 1 else "s"]
	HudBuilder.set_font(subtitle, LOCAL_FONT_SEMIBOLD, 12)
	subtitle.add_theme_color_override("font_color", Color(0.35, 0.35, 0.35))
	card.add_child(subtitle)

	return card


## Shown when a city has nothing left to build (every catalog entry is
## already constructed).
static func build_no_buildings_card() -> Label:
	var label := Label.new()
	label.text = "Nothing left to build here."
	HudBuilder.set_font(label, LOCAL_FONT_SEMIBOLD, 12)
	label.add_theme_color_override("font_color", Color(0.35, 0.35, 0.35))
	return label
