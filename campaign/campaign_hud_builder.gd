extends RefCounted
## Widget construction for the Total War-style HUD (top bar + bottom banner)
## in campaign_ui.gd. Split out to keep campaign_ui.gd under the gdlint file
## line limit - these are pure node-construction helpers that write results
## back onto the campaign_ui instance passed in as `ui`.

const LOCAL_FONT_SEMIBOLD := preload("res://assets/fonts/Baloo2-SemiBold.ttf")
const LOCAL_FONT_BOLD := preload("res://assets/fonts/Baloo2-Bold.ttf")
const HudBuildingsBuilder := preload("res://campaign/campaign_hud_buildings_builder.gd")


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

	# Ornate resource-panel treatment, styled after the reference screenshot's
	# top-left cluster: a parchment-toned card with a gilt double border,
	# offset from the steel-blue bar behind it rather than sitting flush, so
	# the resource row reads as its own framed panel instead of just labels
	# floating on the bar.
	var stats_wrap := MarginContainer.new()
	stats_wrap.add_theme_constant_override("margin_left", 6)
	stats_wrap.add_theme_constant_override("margin_top", 4)
	stats_wrap.add_theme_constant_override("margin_bottom", 4)
	stats_wrap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(stats_wrap)

	var stats_panel := PanelContainer.new()
	stats_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var stats_sb := style_box(Color(0.16, 0.13, 0.08, 0.92), ui.HUD_GOLD, 2)
	stats_sb.set_corner_radius_all(6)
	stats_sb.set_content_margin_all(0)
	stats_wrap.add_child(stats_panel)
	stats_panel.add_theme_stylebox_override("panel", stats_sb)

	# A slightly inset second border sits just inside the outer gilt frame,
	# the "ornate double-rule" look the reference panel uses around its
	# resource icons.
	var inner_margin := MarginContainer.new()
	inner_margin.add_theme_constant_override("margin_left", 3)
	inner_margin.add_theme_constant_override("margin_top", 3)
	inner_margin.add_theme_constant_override("margin_right", 3)
	inner_margin.add_theme_constant_override("margin_bottom", 3)
	stats_panel.add_child(inner_margin)

	var inner_panel := PanelContainer.new()
	var inner_sb := style_box(Color(0.216, 0.176, 0.11, 0.92), Color(0.55, 0.42, 0.2), 1)
	inner_sb.set_corner_radius_all(4)
	inner_margin.add_child(inner_panel)
	inner_panel.add_theme_stylebox_override("panel", inner_sb)

	var stats_margin := MarginContainer.new()
	stats_margin.add_theme_constant_override("margin_left", 14)
	stats_margin.add_theme_constant_override("margin_right", 10)
	stats_margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	inner_panel.add_child(stats_margin)

	var stats_row := HBoxContainer.new()
	stats_row.add_theme_constant_override("separation", 22)
	stats_row.alignment = BoxContainer.ALIGNMENT_BEGIN
	stats_margin.add_child(stats_row)

	for stat in ui.TOP_BAR_STATS:
		stats_row.add_child(_make_top_bar_stat(ui, stat[0], stat[1], stat[2]))

	# The first of the top-bar's round icons is the Economy opener; the rest
	# stay as blank "?" placeholders until they get their own screens.
	var economy_button := _make_top_bar_icon_button(ui, "$", "Economy")
	economy_button.pressed.connect(_toggle_economy_panel.bind(ui))
	stats_row.add_child(economy_button)
	for i in ui.TOP_BAR_PLACEHOLDER_ICON_COUNT - 1:
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
	build_economy_panel(ui)


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


## A clickable variant of the round placeholder icon: same 32px circle, but a
## Button so it can carry a tooltip and fire on press (e.g. the Economy opener).
static func _make_top_bar_icon_button(ui: Node, glyph: String, tooltip: String) -> Button:
	var button := Button.new()
	button.tooltip_text = tooltip
	button.text = glyph
	button.custom_minimum_size = Vector2(32, 32)
	button.size_flags_vertical = Control.SIZE_SHRINK_CENTER
	set_font(button, ui.FONT_BOLD, 16)
	button.add_theme_color_override("font_color", Color.WHITE)
	var normal_sb := style_box(Color(0.35, 0.38, 0.44))
	var hover_sb := style_box(Color(0.32, 0.44, 0.6))
	var pressed_sb := style_box(Color(0.18, 0.26, 0.38))
	for sb in [normal_sb, hover_sb, pressed_sb]:
		sb.corner_radius_top_left = 16
		sb.corner_radius_top_right = 16
		sb.corner_radius_bottom_left = 16
		sb.corner_radius_bottom_right = 16
	button.add_theme_stylebox_override("normal", normal_sb)
	button.add_theme_stylebox_override("hover", hover_sb)
	button.add_theme_stylebox_override("pressed", pressed_sb)
	return button


## The Economy modal: hidden until the top-bar Economy ($) icon toggles it
## (see _toggle_economy_panel). Lists every settlement and the Ordinary Tax it
## owes, grouped by faction. Anchored just under the top bar. Node references
## the toggle/populate helpers need are stashed in metadata so the whole
## feature lives here rather than adding fields to campaign_ui.
static func build_economy_panel(ui: Node) -> void:
	var panel := PanelContainer.new()
	panel.name = "EconomyPanel"
	panel.visible = false
	# The army markers are added to the scene tree after $UI, so within the
	# shared canvas they'd draw over this modal. A high z_index lifts the panel
	# above them (and everything else in the HUD) so it reads as a real overlay.
	panel.z_index = 100
	panel.add_theme_stylebox_override("panel", style_box(ui.HUD_BLUE_DARK, ui.HUD_BLUE, 2))
	anchor_rect(panel, 0.3, 0.09, 0.7, 0.85)
	ui._top_bar.add_child(panel)
	panel.set_meta("manager", ui.manager)

	var margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		margin.add_theme_constant_override("margin_%s" % side, 12)
	panel.add_child(margin)

	var vbox := VBoxContainer.new()
	vbox.add_theme_constant_override("separation", 8)
	margin.add_child(vbox)

	var title := Label.new()
	title.text = "Economy — Ordinary Tax"
	set_font(title, ui.FONT_BOLD, 20)
	title.add_theme_color_override("font_color", Color.WHITE)
	vbox.add_child(title)

	# Column header row.
	var header := _economy_row("Settlement", "Tier", "Tax", ui.FONT_SEMIBOLD, ui.HUD_CREAM)
	vbox.add_child(header)

	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	vbox.add_child(scroll)

	var list := VBoxContainer.new()
	list.add_theme_constant_override("separation", 2)
	list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(list)

	var total := _economy_row("Total", "", "0", ui.FONT_BOLD, Color.WHITE)
	vbox.add_child(total)
	panel.set_meta("list", list)
	panel.set_meta("total_value", total.get_node("Value"))
	panel.set_meta("fonts", [ui.FONT_MEDIUM, ui.FONT_SEMIBOLD])
	panel.set_meta("cream", ui.HUD_CREAM)


## Toggles the Economy modal, repopulating it from live state each time it opens
## so it always reflects the current map (conquest changes who owes what). Bound
## to the top-bar Economy button's `pressed` signal.
static func _toggle_economy_panel(ui: Node) -> void:
	var panel: PanelContainer = ui._top_bar.get_node("EconomyPanel")
	panel.visible = not panel.visible
	if panel.visible:
		_populate_economy(panel)


## Rebuilds the Economy list grouped by faction: a color-coded faction header,
## that faction's settlements (name / tier / Ordinary Tax) sorted by tier, then
## a per-faction subtotal, with a grand total in the pinned footer.
static func _populate_economy(panel: PanelContainer) -> void:
	var list: VBoxContainer = panel.get_meta("list")
	for child in list.get_children():
		child.queue_free()

	var font_medium: Font = panel.get_meta("fonts")[0]
	var font_semibold: Font = panel.get_meta("fonts")[1]
	var cream: Color = panel.get_meta("cream")
	var state: Dictionary = panel.get_meta("manager").get_state()

	var by_faction: Dictionary = {}
	for city in state.get("cities", []):
		var owner := int(city.get("owner", -1))
		if not by_faction.has(owner):
			by_faction[owner] = []
		by_faction[owner].append(city)

	var grand_total := 0
	var first := true
	for faction in state.get("factions", []):
		var owned: Array = by_faction.get(int(faction.get("id", -1)), [])
		if owned.is_empty():
			continue
		if not first:
			var spacer := Control.new()
			spacer.custom_minimum_size = Vector2(0, 10)
			list.add_child(spacer)
		first = false

		var faction_color := Color(String(faction.get("color", "#ffffff")))
		list.add_child(
			_economy_row(
				String(faction.get("name", "?")), "Tier", "Tax", font_semibold, faction_color
			)
		)

		owned.sort_custom(func(a, b): return int(a.get("tier", 0)) > int(b.get("tier", 0)))
		var subtotal := 0
		for city in owned:
			var tax := int(city.get("income", 0))
			subtotal += tax
			list.add_child(
				_economy_row(
					String(city.get("name", "?")),
					str(int(city.get("tier", 0))),
					str(tax),
					font_medium,
					cream
				)
			)
		grand_total += subtotal
		list.add_child(_economy_row("Subtotal", "", str(subtotal), font_semibold, Color.WHITE))

	panel.get_meta("total_value").text = str(grand_total)


## One three-column row (name / tier / tax) for the Economy list. The tax value
## label is named "Value" so callers can find it to rewrite later.
static func _economy_row(
	name_text: String, tier_text: String, tax_text: String, font: Font, color: Color
) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)

	var name_label := Label.new()
	name_label.text = name_text
	name_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	set_font(name_label, font, 14)
	name_label.add_theme_color_override("font_color", color)
	row.add_child(name_label)

	var tier_label := Label.new()
	tier_label.text = tier_text
	tier_label.custom_minimum_size = Vector2(60, 0)
	tier_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	set_font(tier_label, font, 14)
	tier_label.add_theme_color_override("font_color", color)
	row.add_child(tier_label)

	var tax_label := Label.new()
	tax_label.name = "Value"
	tax_label.text = tax_text
	tax_label.custom_minimum_size = Vector2(80, 0)
	tax_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	set_font(tax_label, font, 14)
	tax_label.add_theme_color_override("font_color", color)
	row.add_child(tax_label)

	return row


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
	HudBuildingsBuilder.build_buildings_panel(ui)
	_build_end_turn_banner(ui)


static func _build_city_panel(ui: Node) -> void:
	var panel := Control.new()
	panel.name = "CityPanel"
	anchor_rect(panel, 0.017, 0.617, 0.187, 0.99)
	panel.visible = false
	# Army markers (campaign/army_layer.gd) are added to the scene tree after
	# $UI, so within the shared canvas they'd draw over this panel by tree
	# order alone. A z_index above the army layer's default 0 (same pattern as
	# the Economy modal above) keeps the settlement panel on top whenever a
	# city is selected, instead of army icons drawing over it.
	panel.z_index = 50
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


## ---------------------------------------------------------------------------
## Battle result modal: a centred popup shown after the battle-clash
## animation (campaign/battle_animation.gd) finishes, announcing the winning
## faction. Built once, hidden by default; campaign_ui._show_battle_result_
## modal() writes the text and toggles visibility, awaiting the OK button's
## `pressed` signal so turn advancement blocks until the player acknowledges
## it (see campaign_ui.gd's battle queue).
## ---------------------------------------------------------------------------


static func build_battle_modal(ui: Node) -> void:
	var backdrop := ColorRect.new()
	backdrop.name = "BattleModalBackdrop"
	backdrop.color = Color(0, 0, 0, 0.55)
	backdrop.mouse_filter = Control.MOUSE_FILTER_STOP
	anchor_rect(backdrop, 0.0, 0.0, 1.0, 1.0)
	backdrop.visible = false
	ui._ui.add_child(backdrop)
	ui._battle_modal = backdrop

	var panel := PanelContainer.new()
	panel.name = "BattleResultPanel"
	panel.add_theme_stylebox_override("panel", style_box(ui.HUD_BLUE_DARK, ui.HUD_CREAM, 3))
	anchor_rect(panel, 0.34, 0.36, 0.66, 0.62)
	backdrop.add_child(panel)

	var margin := MarginContainer.new()
	for side in ["left", "top", "right", "bottom"]:
		margin.add_theme_constant_override("margin_%s" % side, 18)
	panel.add_child(margin)

	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 14)
	margin.add_child(box)

	ui._battle_modal_title_label = Label.new()
	set_font(ui._battle_modal_title_label, LOCAL_FONT_BOLD, 22)
	ui._battle_modal_title_label.add_theme_color_override("font_color", ui.HUD_CREAM)
	ui._battle_modal_title_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	box.add_child(ui._battle_modal_title_label)

	ui._battle_modal_message_label = Label.new()
	set_font(ui._battle_modal_message_label, LOCAL_FONT_SEMIBOLD, 15)
	ui._battle_modal_message_label.add_theme_color_override("font_color", ui.HUD_CREAM)
	ui._battle_modal_message_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	ui._battle_modal_message_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(ui._battle_modal_message_label)

	var ok_wrap := CenterContainer.new()
	box.add_child(ok_wrap)

	ui._battle_modal_ok_button = Button.new()
	ui._battle_modal_ok_button.text = "OK"
	ui._battle_modal_ok_button.custom_minimum_size = Vector2(90, 32)
	ui._battle_modal_ok_button.add_theme_color_override("font_color", Color.WHITE)
	set_font(ui._battle_modal_ok_button, LOCAL_FONT_BOLD, 15)
	ui._battle_modal_ok_button.add_theme_stylebox_override(
		"normal", style_box(Color(0.55, 0.13, 0.05), Color(0.85, 0.45, 0.1), 2)
	)
	ui._battle_modal_ok_button.add_theme_stylebox_override(
		"hover", style_box(Color(0.65, 0.18, 0.07), Color(0.85, 0.45, 0.1), 2)
	)
	ui._battle_modal_ok_button.add_theme_stylebox_override(
		"pressed", style_box(Color(0.45, 0.1, 0.04), Color(0.85, 0.45, 0.1), 2)
	)
	ok_wrap.add_child(ui._battle_modal_ok_button)


static func format_stat(n: int) -> String:
	if n >= 1000:
		return "%.1fK" % (n / 1000.0)
	return str(n)
