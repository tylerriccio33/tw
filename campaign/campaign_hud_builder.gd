extends RefCounted
## Widget construction for the Total War-style HUD (top bar + bottom banner)
## in campaign_ui.gd. Split out to keep campaign_ui.gd under the gdlint file
## line limit - these are pure node-construction helpers that write results
## back onto the campaign_ui instance passed in as `ui`.

const LOCAL_FONT_SEMIBOLD := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const LOCAL_FONT_BOLD := preload("res://assets/fonts/Baloo2-Bold.ttf")


static func style_box(
	bg: Color, border_color: Color = Color.TRANSPARENT, border_w: int = 0
) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	if border_w > 0:
		sb.set_border_width_all(border_w)
		sb.border_color = border_color
	return sb


static func set_font(control: Control, font: Font, size: int = -1) -> void:
	control.add_theme_font_override("font", font)
	if size > 0:
		control.add_theme_font_size_override("font_size", size)


static func anchor_rect(
	control: Control, left: float, top: float, right: float, bottom: float
) -> void:
	control.anchor_left = left
	control.anchor_top = top
	control.anchor_right = right
	control.anchor_bottom = bottom
	control.offset_left = 0
	control.offset_top = 0
	control.offset_right = 0
	control.offset_bottom = 0


## ---------------------------------------------------------------------------
## Top bar: a Total War-style resource strip (treasury/deficit/food/season/
## year plus a settlements/armies/wiki button cluster), mirroring the layout
## of the bottom banner below - built once, rendering only (no button
## behavior wired up yet).
## ---------------------------------------------------------------------------


static func build_top_bar(ui: Node) -> void:
	ui._top_bar.mouse_filter = Control.MOUSE_FILTER_IGNORE

	var bar := PanelContainer.new()
	bar.name = "Bar"
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	bar.add_theme_stylebox_override("panel", style_box(ui.HUD_BLUE_DARK))
	anchor_rect(bar, 0.0, 0.0, 1.0, 0.065)
	ui._top_bar.add_child(bar)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 0)
	row.set_anchors_preset(Control.PRESET_FULL_RECT)
	bar.add_child(row)

	var stats_margin := MarginContainer.new()
	stats_margin.add_theme_constant_override("margin_left", 16)
	stats_margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(stats_margin)

	var stats_row := HBoxContainer.new()
	stats_row.add_theme_constant_override("separation", 22)
	stats_row.alignment = BoxContainer.ALIGNMENT_BEGIN
	stats_margin.add_child(stats_row)

	for stat in ui.TOP_BAR_STATS:
		stats_row.add_child(_make_top_bar_stat(ui, stat[0], stat[1], stat[2]))

	for i in ui.TOP_BAR_PLACEHOLDER_ICON_COUNT:
		stats_row.add_child(_make_top_bar_placeholder_icon())

	var buttons_row := HBoxContainer.new()
	buttons_row.add_theme_constant_override("separation", 2)
	row.add_child(buttons_row)

	ui.settlements_button = _make_top_bar_button(ui, "🏘", "Settlements")
	ui.armies_button = _make_top_bar_button(ui, "⚔", "Armies")
	ui.wiki_button = _make_top_bar_button(ui, "📖", "Wiki")
	ui.log_button = _make_top_bar_button(ui, "📜", "Log")
	buttons_row.add_child(ui.settlements_button)
	buttons_row.add_child(ui.armies_button)
	buttons_row.add_child(ui.wiki_button)
	buttons_row.add_child(ui.log_button)

	_build_log_panel(ui)


## Hidden by default; the log button in the top bar toggles it. Anchored under
## the top bar's right edge so it drops down near the icon that opens it,
## rather than the old always-on log strip that used to sit over the map.
static func _build_log_panel(ui: Node) -> void:
	var panel := PanelContainer.new()
	panel.name = "LogPanel"
	panel.visible = false
	panel.add_theme_stylebox_override("panel", style_box(ui.HUD_BLUE_DARK, ui.HUD_BLUE, 2))
	anchor_rect(panel, 0.6, 0.07, 0.998, 0.4)
	ui._top_bar.add_child(panel)
	ui._log_panel = panel

	var margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		margin.add_theme_constant_override("margin_%s" % side, 8)
	panel.add_child(margin)

	ui.log_label = RichTextLabel.new()
	ui.log_label.bbcode_enabled = true
	ui.log_label.scroll_following = true
	margin.add_child(ui.log_label)


## ---------------------------------------------------------------------------
## Turn indicator: a small, centred, top-middle widget (classic Total War
## turn-order style) showing whichever faction is acting this turn - an
## empty circle placeholder (no faction icon assets yet) with the faction's
## name below it. Built once here; campaign_ui._refresh_turn_indicator()
## rewrites the label text on every turn/faction change.
## ---------------------------------------------------------------------------


static func build_turn_indicator(ui: Node) -> void:
	ui._turn_indicator.mouse_filter = Control.MOUSE_FILTER_IGNORE

	var wrapper := VBoxContainer.new()
	wrapper.name = "TurnIndicator"
	wrapper.alignment = BoxContainer.ALIGNMENT_CENTER
	wrapper.add_theme_constant_override("separation", 4)
	# Centred just under the top bar, straddling the viewport's horizontal
	# midpoint so it stays centred at any window size.
	anchor_rect(wrapper, 0.42, 0.07, 0.58, 0.16)
	ui._turn_indicator.add_child(wrapper)

	var circle := PanelContainer.new()
	circle.custom_minimum_size = Vector2(44, 44)
	circle.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	var circle_sb := StyleBoxFlat.new()
	circle_sb.bg_color = Color.TRANSPARENT
	circle_sb.border_color = ui.HUD_CREAM
	circle_sb.set_border_width_all(3)
	circle_sb.corner_radius_top_left = 22
	circle_sb.corner_radius_top_right = 22
	circle_sb.corner_radius_bottom_left = 22
	circle_sb.corner_radius_bottom_right = 22
	circle.add_theme_stylebox_override("panel", circle_sb)
	wrapper.add_child(circle)

	var name_label := Label.new()
	name_label.text = "Faction"
	name_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	name_label.size_flags_horizontal = Control.SIZE_SHRINK_CENTER
	set_font(name_label, ui.FONT_SEMIBOLD, 15)
	name_label.add_theme_color_override("font_color", Color.WHITE)
	name_label.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.85))
	name_label.add_theme_constant_override("shadow_offset_x", 1)
	name_label.add_theme_constant_override("shadow_offset_y", 1)
	wrapper.add_child(name_label)
	ui._turn_indicator_name_label = name_label


static func _make_top_bar_stat(
	ui: Node, icon: String, label_text: String, value_text: String
) -> HBoxContainer:
	var cell := HBoxContainer.new()
	cell.add_theme_constant_override("separation", 6)

	var icon_label := Label.new()
	icon_label.text = icon
	icon_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	cell.add_child(icon_label)

	var text_vbox := VBoxContainer.new()
	text_vbox.add_theme_constant_override("separation", 0)
	cell.add_child(text_vbox)

	var name_label := Label.new()
	name_label.text = label_text
	set_font(name_label, ui.FONT_MEDIUM, 10)
	name_label.add_theme_color_override("font_color", Color(0.75, 0.78, 0.85))
	text_vbox.add_child(name_label)

	var value_label := Label.new()
	value_label.text = value_text
	set_font(value_label, ui.FONT_BOLD, 15)
	value_label.add_theme_color_override("font_color", Color.WHITE)
	text_vbox.add_child(value_label)
	ui._top_bar_stat_value_labels[label_text] = value_label

	return cell


static func _make_top_bar_placeholder_icon() -> PanelContainer:
	var icon_circle := PanelContainer.new()
	var circle_sb := style_box(Color(0.35, 0.38, 0.44))
	circle_sb.corner_radius_top_left = 16
	circle_sb.corner_radius_top_right = 16
	circle_sb.corner_radius_bottom_left = 16
	circle_sb.corner_radius_bottom_right = 16
	icon_circle.add_theme_stylebox_override("panel", circle_sb)
	icon_circle.custom_minimum_size = Vector2(32, 32)
	var glyph_label := Label.new()
	glyph_label.text = "?"
	glyph_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	glyph_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	glyph_label.add_theme_color_override("font_color", Color(0.85, 0.85, 0.85))
	icon_circle.add_child(glyph_label)
	return icon_circle


static func _make_top_bar_button(ui: Node, icon: String, tooltip: String) -> Button:
	var button := Button.new()
	button.tooltip_text = tooltip
	button.text = icon
	button.custom_minimum_size = Vector2(52, 0)
	set_font(button, ui.FONT_SEMIBOLD, 18)
	button.add_theme_color_override("font_color", Color.WHITE)
	var normal_sb := style_box(ui.HUD_BLUE)
	var hover_sb := style_box(Color(0.32, 0.44, 0.6))
	var pressed_sb := style_box(Color(0.18, 0.26, 0.38))
	button.add_theme_stylebox_override("normal", normal_sb)
	button.add_theme_stylebox_override("hover", hover_sb)
	button.add_theme_stylebox_override("pressed", pressed_sb)
	return button


## ---------------------------------------------------------------------------
## Bottom banner: a Total War-style HUD strip (city info panel, buildings
## row, end-turn ribbon) built once here and then just refreshed with new
## label text/colors on every turn/battle event. Positions are anchor
## fractions of the full viewport, measured off a reference screenshot, so
## the banner keeps its on-screen proportions across window sizes.
## ---------------------------------------------------------------------------


static func build_bottom_banner(ui: Node) -> void:
	ui.bottom_banner.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_build_city_panel(ui)
	_build_buildings_panel(ui)
	_build_end_turn_banner(ui)


static func _build_city_panel(ui: Node) -> void:
	var panel := Control.new()
	panel.name = "CityPanel"
	anchor_rect(panel, 0.017, 0.617, 0.187, 0.99)
	panel.visible = false
	ui.bottom_banner.add_child(panel)
	ui._city_panel = panel

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 0)
	vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	panel.add_child(vbox)

	# Header: navy bar with the city name and a faction-colored tab.
	var header := PanelContainer.new()
	header.add_theme_stylebox_override("panel", style_box(ui.HUD_BLUE))
	header.custom_minimum_size = Vector2(0, 40)
	vbox.add_child(header)

	var header_row := HBoxContainer.new()
	header_row.add_theme_constant_override("separation", 8)
	header.add_child(header_row)

	var margin := MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 10)
	margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header_row.add_child(margin)

	ui._city_panel_name_label = Label.new()
	ui._city_panel_name_label.text = "Burgos"
	set_font(ui._city_panel_name_label, ui.FONT_BOLD, 20)
	ui._city_panel_name_label.add_theme_color_override("font_color", Color.WHITE)
	ui._city_panel_name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	margin.add_child(ui._city_panel_name_label)

	ui._city_panel_owner_tab = ColorRect.new()
	ui._city_panel_owner_tab.custom_minimum_size = Vector2(36, 40)
	ui._city_panel_owner_tab.color = Color.INDIAN_RED
	header_row.add_child(ui._city_panel_owner_tab)

	# Back/close button - collapses the settlement panel back to the core
	# HUD and clears the selection, so it doesn't linger on the next click.
	var close_button := Button.new()
	close_button.name = "CloseButton"
	close_button.text = "✕"
	close_button.focus_mode = Control.FOCUS_NONE
	close_button.custom_minimum_size = Vector2(32, 40)
	set_font(close_button, ui.FONT_BOLD, 16)
	close_button.add_theme_color_override("font_color", Color.WHITE)
	close_button.add_theme_color_override("font_hover_color", Color.WHITE)
	close_button.add_theme_stylebox_override("normal", style_box(ui.HUD_BLUE))
	close_button.add_theme_stylebox_override("hover", style_box(Color(0.35, 0.46, 0.62)))
	close_button.add_theme_stylebox_override("pressed", style_box(ui.HUD_BLUE_DARK))
	close_button.pressed.connect(ui._on_settlement_panel_close_pressed)
	header_row.add_child(close_button)

	# Body: parchment background holding the governor row and stat list.
	var body := PanelContainer.new()
	body.add_theme_stylebox_override("panel", style_box(ui.HUD_CREAM))
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
	gov_button.add_theme_stylebox_override("panel", style_box(Color(0.6, 0.6, 0.58)))
	gov_button.custom_minimum_size = Vector2(60, 60)
	gov_row.add_child(gov_button)

	var gov_label := Label.new()
	gov_label.text = "Gov..."
	set_font(gov_label, ui.FONT_MEDIUM, 13)
	gov_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	gov_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_LEFT
	gov_label.vertical_alignment = VERTICAL_ALIGNMENT_TOP
	gov_button.add_child(gov_label)

	var perk_label := Label.new()
	perk_label.text = "Perk pts   0"
	ui._city_panel_perk_label = perk_label
	set_font(perk_label, ui.FONT_SEMIBOLD, 15)
	perk_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	perk_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	perk_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	gov_row.add_child(perk_label)

	var menu_pill := PanelContainer.new()
	var pill_sb := style_box(ui.HUD_BLUE)
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

	for i in ui.STAT_ROWS.size():
		var row_def: Array = ui.STAT_ROWS[i]
		# A visual gap before "Region wea..." matches the reference, which
		# groups income-adjacent stats apart from the population stats above.
		if row_def[0] == "region_wealth":
			var spacer := Control.new()
			spacer.custom_minimum_size = Vector2(0, 10)
			stats_list.add_child(spacer)
		stats_list.add_child(_make_stat_row(ui, row_def[0], row_def[1], row_def[2]))


static func _make_stat_row(
	ui: Node, key: String, label_text: String, icon: String
) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 6)

	var icon_label := Label.new()
	icon_label.text = icon
	icon_label.add_theme_color_override("font_color", ui.HUD_BLUE)
	icon_label.custom_minimum_size = Vector2(20, 0)
	row.add_child(icon_label)

	var name_label := Label.new()
	name_label.text = label_text
	set_font(name_label, ui.FONT_MEDIUM)
	name_label.add_theme_color_override("font_color", Color(0.25, 0.25, 0.25))
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(name_label)

	var value_label := Label.new()
	value_label.text = "0"
	value_label.add_theme_color_override("font_color", Color(0.05, 0.05, 0.05))
	set_font(value_label, ui.FONT_BOLD, 15)
	value_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	row.add_child(value_label)
	ui._city_stat_value_labels[key] = value_label

	return row


static func _build_buildings_panel(ui: Node) -> void:
	var panel := Control.new()
	panel.name = "BuildingsPanel"
	anchor_rect(panel, 0.19, 0.824, 0.762, 0.99)
	panel.visible = false
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
	cards_row.add_theme_stylebox_override("panel", style_box(ui.HUD_CREAM))
	cards_row.set_anchors_preset(Control.PRESET_FULL_RECT)

	var cards_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		cards_margin.add_theme_constant_override("margin_%s" % side, 4)
	cards_row.add_child(cards_margin)

	var cards_hbox := HBoxContainer.new()
	cards_hbox.add_theme_constant_override("separation", 4)
	cards_margin.add_child(cards_hbox)

	for b in ui.BUILDINGS:
		cards_hbox.add_child(_make_building_card(b["name"], b["level"], false))
	for name in ui.LOCKED_BUILDINGS:
		cards_hbox.add_child(_make_building_card(name, -1, true))

	var red_strip := PanelContainer.new()
	red_strip.add_theme_stylebox_override("panel", style_box(ui.HUD_MAROON))
	red_strip.custom_minimum_size = Vector2(56, 0)
	cards_hbox.add_child(red_strip)

	var red_vbox := VBoxContainer.new()
	red_vbox.alignment = BoxContainer.ALIGNMENT_CENTER
	red_vbox.add_theme_constant_override("separation", 8)
	red_vbox.set_anchors_preset(Control.PRESET_FULL_RECT)
	red_strip.add_child(red_vbox)
	for glyph in ["🏰", "⛵"]:
		var icon_circle := PanelContainer.new()
		var circle_sb := style_box(Color(0.15, 0.15, 0.15))
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
	panel.add_theme_stylebox_override("panel", style_box(ui.HUD_CREAM))
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
	set_font(title_label, LOCAL_FONT_BOLD, 15)
	title_label.add_theme_color_override("font_color", Color(0.2, 0.2, 0.2))
	header.add_child(title_label)

	var count_label := Label.new()
	var total := 0
	for u in units:
		total += int(u["count"])
	count_label.text = "%d units" % total if units.size() > 0 else ""
	set_font(count_label, LOCAL_FONT_SEMIBOLD, 11)
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
		set_font(empty_label, ui.FONT_MEDIUM, 12)
		empty_label.add_theme_color_override("font_color", Color(0.5, 0.5, 0.5))
		col.add_child(empty_label)
		return col

	for u in units:
		col.add_child(_make_unit_row(ui, u["name"], u["icon"], int(u["count"])))

	return col


static func _make_unit_row(ui: Node, unit_name: String, icon: String, count: int) -> PanelContainer:
	var row := PanelContainer.new()
	row.add_theme_stylebox_override("panel", style_box(Color(1.0, 1.0, 1.0, 0.5)))

	var row_margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		row_margin.add_theme_constant_override("margin_%s" % side, 4)
	row.add_child(row_margin)

	var hbox := HBoxContainer.new()
	hbox.add_theme_constant_override("separation", 8)
	row_margin.add_child(hbox)

	var icon_circle := PanelContainer.new()
	var circle_sb := style_box(ui.HUD_BLUE)
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
	set_font(name_label, LOCAL_FONT_SEMIBOLD, 13)
	name_label.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	name_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hbox.add_child(name_label)

	var count_label := Label.new()
	count_label.text = str(count)
	set_font(count_label, LOCAL_FONT_BOLD, 14)
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
	set_font(tab, LOCAL_FONT_SEMIBOLD)
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
	var sb := style_box(bg)
	sb.content_margin_left = side_margin
	sb.content_margin_right = side_margin
	return sb


static func _make_building_card(name: String, level: int, locked: bool) -> VBoxContainer:
	var card := VBoxContainer.new()
	card.add_theme_constant_override("separation", 2)
	card.custom_minimum_size = Vector2(84, 0)

	var image_panel := PanelContainer.new()
	image_panel.add_theme_stylebox_override(
		"panel", style_box(Color(0.7, 0.7, 0.7) if locked else Color.WHITE, Color.BLACK, 1)
	)
	image_panel.custom_minimum_size = Vector2(0, 70)
	card.add_child(image_panel)

	if not locked:
		var level_label := Label.new()
		level_label.text = "Lv.%d" % level
		set_font(level_label, LOCAL_FONT_BOLD, 13)
		level_label.add_theme_color_override("font_color", Color.BLACK)
		image_panel.add_child(level_label)
	elif name == "Mine":
		var q_label := Label.new()
		q_label.text = "?"
		set_font(q_label, LOCAL_FONT_BOLD, 28)
		q_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		q_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		q_label.add_theme_color_override("font_color", Color(0.1, 0.1, 0.1))
		image_panel.add_child(q_label)

	var caption := Label.new()
	caption.text = name
	set_font(caption, LOCAL_FONT_SEMIBOLD)
	caption.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	caption.add_theme_color_override("font_color", Color(0.15, 0.15, 0.15))
	card.add_child(caption)

	return card


static func _build_end_turn_banner(ui: Node) -> void:
	ui.end_turn_button = Button.new()
	ui.end_turn_button.name = "EndTurnButton"
	anchor_rect(ui.end_turn_button, 0.89, 0.87, 0.965, 0.93)
	ui.end_turn_button.text = "END TURN 1"
	ui.end_turn_button.add_theme_color_override("font_color", Color.WHITE)
	set_font(ui.end_turn_button, ui.FONT_BOLD, 16)
	var sb := style_box(Color(0.55, 0.13, 0.05), Color(0.85, 0.45, 0.1), 2)
	ui.end_turn_button.add_theme_stylebox_override("normal", sb)
	ui.end_turn_button.add_theme_stylebox_override(
		"hover", style_box(Color(0.65, 0.18, 0.07), Color(0.85, 0.45, 0.1), 2)
	)
	ui.end_turn_button.add_theme_stylebox_override(
		"pressed", style_box(Color(0.45, 0.1, 0.04), Color(0.85, 0.45, 0.1), 2)
	)
	ui.bottom_banner.add_child(ui.end_turn_button)
	ui.end_turn_button.pressed.connect(ui._on_end_turn_pressed)


static func format_stat(n: int) -> String:
	if n >= 1000:
		return "%.1fK" % (n / 1000.0)
	return str(n)
