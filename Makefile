GODOT ?= godot

.PHONY: help addons import shot diff accept smoke api clean-shots

help:
	@echo "make addons   - vendor the Terrain3D GDExtension into addons/"
	@echo "make import   - reimport assets and register the GDExtension"
	@echo "make shot     - render every preset in config/shots.json to shots/current/"
	@echo "make diff     - PSNR table + shots/contact_sheet.png vs shots/golden/"
	@echo "make accept   - promote shots/current/ to shots/golden/"
	@echo "make smoke    - verify the Terrain3D extension loads and its API is present"
	@echo "make api      - dump the Terrain3D API surface (properties, methods, enums)"

addons:
	python3 tools/fetch_addons.py

# Godot only registers a GDExtension after a project import, so a fresh clone
# renders an empty scene until this has run at least once.
import:
	$(GODOT) --headless --import

smoke:
	$(GODOT) --headless --script res://tools/smoke.gd

api:
	$(GODOT) --headless --script res://tools/introspect.gd

# Runs windowed on purpose: Godot cannot capture a viewport in --headless mode,
# there is no display server to render with. The window is 128x128 (see
# project.godot) while capture happens through a full-resolution SubViewport.
shot:
	@rm -f shots/current/*.png
	$(GODOT)

diff:
	@uv run tools/diff_shots.py

accept:
	@mkdir -p shots/golden
	@cp shots/current/*.png shots/golden/
	@echo "promoted $$(ls shots/current/*.png | wc -l | tr -d ' ') shot(s) to shots/golden/"

clean-shots:
	rm -rf shots/current shots/contact_sheet.png
