GODOT ?= godot

.PHONY: help armies import campaign campaign-test campaign-smoke check play play-shot hud-shot clean-shots map-editor map-editor-test map-editor-preview promote-map

ci: ## Commit everything and push straight to main
	@echo "Staging everything"
	@git add .
	@echo "Running pre-commit"
	@uvx prek run --all-files
	@echo "Committing with message: $$MSG"
	@git commit -m "$$MSG"
	@echo "Pushing to origin main"
	@git push origin HEAD:main
	@echo "Done"

help:
	@echo "make armies   - vendor Quaternius CC0 LowPoly Animated Knight into assets/armies/ for standing-army models"
	@echo "make import   - reimport assets and register the GDExtension"
	@echo "make check    - parse every .gd file for errors (incl. type-inference warnings) in ~1s, no render"
	@echo "make campaign      - build the campaign-map Rust GDExtension and install it into addons/campaign/bin/"
	@echo "make campaign-test - run the Rust unit tests for the campaign-map game logic"
	@echo "make campaign-smoke - headless smoke test that the campaign GDExtension loads"
	@echo "make play          - build the extension and open the campaign map in a window"
	@echo "make play-shot     - screenshot the whole campaign window to shots/play/play.png"
	@echo "make hud-shot      - screenshot just the bottom HUD banner to shots/play/hud.png"
	@echo "make map-editor    - launch the browser-based territory border editor (tools/map_editor)"
	@echo "make map-editor-test - run the map editor's pytest suite (project I/O, raster round-trip, coastline classification)"
	@echo "make map-editor-preview - render dev_map_data/project.json to a single PNG (tools/map_editor/dev_map_data/preview.png), no browser needed"
	@echo "make promote-map   - copy the traced dev map (tools/map_editor/dev_map_data) into campaign/map_data for the game to use"

armies:
	@uv run tools/fetch_armies.py

# Godot only registers a GDExtension after a project import, so a fresh clone
# renders an empty scene until this has run at least once.
import:
	$(GODOT) --headless --import

# --check-only parses a script and reports errors, but still exits 0 on a
# parse error - the same lie a plain godot invocation works around, so this
# goes through godot_gate.sh too. Combined with treat_warnings_as_errors in
# project.godot, `var x := some_untyped_call()` fails here in about a second.
check:
	@fail=0; \
	for f in tools/*.gd campaign/*.gd; do \
		echo "checking $$f"; \
		if ! GODOT=$(GODOT) ./tools/godot_gate.sh --headless --check-only --script "res://$$f"; then \
			echo "FAIL: $$f"; \
			fail=1; \
		fi; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "check OK"

# Builds the campaign-map GDExtension in release mode and installs the dylib
# where campaign.gdextension expects it. Re-run after any rust/campaign/ change.
campaign:
	cd rust/campaign && cargo build --release
	@mkdir -p addons/campaign/bin
	cp rust/campaign/target/release/libcampaign.dylib addons/campaign/bin/libcampaign.dylib

campaign-test:
	cd rust/campaign && cargo test

campaign-smoke:
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless --script res://tools/campaign_smoke.gd

play: campaign
	$(GODOT) res://campaign/campaign.tscn --resolution $(or $(RESOLUTION),1280x800)

# Launches `make play` windowed, waits for it to settle, and takes an
# OS-level screenshot of the window to shots/play/play.png. macOS only.
play-shot: campaign
	GODOT=$(GODOT) ./tools/play_shot.sh

# Reuses play-shot's full-window capture and crops it down to just the
# bottom HUD banner (city panel/buildings row/end-turn ribbon), so reviewing
# a HUD tweak doesn't mean eyeballing it inside a full 1280x800 map shot.
# RESOLUTION must match whatever play-shot itself was given, since the crop
# needs it to know where the OS titlebar chrome ends and game content
# starts - both default to 1280x800 so this is a no-op unless overridden.
hud-shot: play-shot
	@RESOLUTION=$(or $(RESOLUTION),1280x800) uv run tools/hud_shot.py

clean-shots:
	rm -rf shots/play

# Runs the territory-border tracing tool at http://localhost:8765. Exports
# from the editor always land in tools/map_editor/dev_map_data/ - nothing
# touches the live campaign/map_data/ until you run `make promote-map`.
map-editor:
	cd tools/map_editor && uv run server.py

map-editor-test:
	cd tools/map_editor && uv run --group dev pytest -q

# Renders dev_map_data/project.json as a single PNG (region colors
# overlaid on the real terrain backdrop) without opening a browser -
# tools/map_editor/dev_map_data/preview.png. Runs the same
# validate_project + export_project path the editor's Export button
# uses, so any invalid polygon (self-intersecting, revisits a point,
# color collision, off-map) gets outlined in red on the image and
# listed on stdout instead of silently producing a bad export.
map-editor-preview:
	cd tools/map_editor && uv run preview.py

# Copies the traced dev map into campaign/map_data/, where
# campaign/province_map.gd actually loads region_map.png + regions.txt
# from. Run this after tracing/editing borders in `make map-editor` and
# exporting, once you're happy with the result.
#
# Also forces a Godot reimport: Godot caches region_map.png as a .ctex
# under .godot/imported/, keyed by content hash, and `make play` never
# triggers a reimport itself - overwriting the source PNG alone leaves the
# game rendering the stale cached texture, so newly-added territories
# silently don't show up until this runs.
promote-map:
	@test -f tools/map_editor/dev_map_data/region_map.png -a -f tools/map_editor/dev_map_data/regions.txt \
		|| (echo "No dev map to promote - export from the map editor first (make map-editor)."; exit 1)
	cp tools/map_editor/dev_map_data/region_map.png campaign/map_data/region_map.png
	cp tools/map_editor/dev_map_data/regions.txt campaign/map_data/regions.txt
	$(GODOT) --headless --import
	@echo "Promoted dev map -> campaign/map_data/ and reimported"
