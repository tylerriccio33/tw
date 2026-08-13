GODOT ?= godot

.PHONY: help armies import campaign campaign-test campaign-smoke check play play-shot hud-shot clean-shots map-editor map-editor-test map-editor-test-full map-editor-js-check map-editor-preview map-editor-add-cities map-package-init map-package-check promote-map gut gut-test render-shots render-test render-test-update

ci: ## Commit everything and push straight to main
	@echo "Staging everything"
	@git add .
	@echo "Running pre-commit"
	@uvx prek run --all-files --fail-fast
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
	@echo "make map-editor    - launch the browser-based layered map editor (tools/map_editor)"
	@echo "make map-editor-test - run the map editor's fast pytest suite (excludes slow/realistic-backdrop tests)"
	@echo "make map-editor-test-full - run the map editor's full pytest suite, including slow/realistic-backdrop and browser-driven e2e tests (needs a one-time 'cd tools/map_editor && uv run playwright install chromium')"
	@echo "make map-editor-js-check - eslint + prettier --check + tsc --noEmit + stylelint over tools/map_editor/static"
	@echo "make map-editor-preview - composite the whole layer stack to one PNG (tools/map_editor/dev_map_data/preview.png), no browser needed"
	@echo "make map-editor-add-cities - bulk-add cities from a gazetteer CSV via the map's georef (MIN_POP=N, TOP=N, DRY=1)"
	@echo "make map-package-init - create a fresh map package from a backdrop (SEED=N for placeholder provinces)"
	@echo "make map-package-check - validate the dev map package without exporting"
	@echo "make promote-map   - copy the dev map package (tools/map_editor/dev_map_data) into campaign/map_data for the game to use"
	@echo "make gut           - vendor the GUT addon (GDScript unit testing) into addons/gut/"
	@echo "make gut-test      - run the GDScript unit tests under tests/unit/ with GUT"
	@echo "make render-test        - SSIM-gate a fresh capture of the campaign scene against tests/golden/"
	@echo "make render-test-update - accept the current capture as the new tests/golden/ baseline"

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

# Vendors GUT (https://github.com/bitwes/Gut) into addons/gut/, the same way
# `make armies` vendors the knight model. addons/gut/ is gitignored, so a
# fresh clone needs this once before gut-test works.
gut:
	@uv run tools/fetch_gut.py

# Runs the GDScript unit tests under tests/unit/ headless, per .gutconfig.json.
# Exits nonzero on any failure, so it's CI-able like campaign-smoke.
gut-test: gut
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless -s res://addons/gut/gut_cmdln.gd -gexit

play: campaign
	$(GODOT) res://campaign/campaign.tscn --resolution $(or $(RESOLUTION),1280x800)

# Launches `make play` windowed, waits for it to settle, and takes an
# OS-level screenshot of the window to shots/play/play.png. macOS only.
play-shot: campaign
	GODOT=$(GODOT) ./tools/play_shot.sh

# Renders the campaign to shots/play/shot.png from inside the engine, with
# no window capture at all. play-shot needs Accessibility and Screen
# Recording granted to whatever terminal runs it, and without them it either
# can't find the window or writes a black frame; this works regardless. Use
# it for reviewing a visual change - play-shot is still the one that shows
# real window chrome. Override the size with RESOLUTION=WxH.
shot: campaign
	@mkdir -p shots/play
	$(GODOT) -s tools/shoot.gd -- res://campaign/campaign.tscn \
		shots/play/shot.png $(or $(RESOLUTION),1280x800)

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

# Captures the deterministic post-boot campaign scene via tools/shoot.gd for
# render-test to compare against tests/golden/. `armies` is a prerequisite
# because army_marker.gd preloads an assets/armies/ icon at parse time - on
# a machine that never ran `make armies`, that preload comes back empty and
# the capture would silently render a blank piece instead of failing.
render-shots: campaign armies
	@mkdir -p shots/render_test/actual
	$(GODOT) -s tools/shoot.gd -- res://campaign/campaign.tscn \
		shots/render_test/actual/campaign_boot.png 1280x800

# SSIM-diffs the fresh capture above against tests/golden/*.png and fails if
# either drops below threshold (tools/render_test.py) - the automated half
# of what the visual-change-review workflow does by eye: a shader that
# stopped compiling, a HUD panel drawn in front of the map instead of
# behind it, a marker that silently lost its texture. Prints where it wrote
# a diff heatmap on failure.
render-test: render-shots
	@uv run tools/render_test.py compare

# Overwrites tests/golden/ with the current capture. Run once you've looked
# at a `make render-test` failure's diff heatmap (shots/render_test/diff/)
# and confirmed the change was intentional - this has no review step of its
# own, it just accepts whatever render-shots produced.
render-test-update: render-shots
	@uv run tools/render_test.py update

# Runs the layered map editor at http://localhost:8765. Everything it
# writes lands in tools/map_editor/dev_map_data/ - nothing touches the
# live campaign/map_data/ until you run `make promote-map`.
map-editor:
	cd tools/map_editor && uv run server.py

map-editor-test:
	cd tools/map_editor && uv run --group dev pytest -q

# Includes test_realistic.py and test_growth_realistic.py, which are
# marked `slow` and skipped by default (see pyproject.toml's addopts).
# Together they take ~4-5 minutes because they drive growth/export to
# completion against the real campaign backdrop. Run this before
# `make promote-map` or any other release-shaped map change - the fast
# default suite alone doesn't exercise real-scale coastline geometry.
#
# Also includes tests/test_e2e.py, which drives the real editor UI in a
# headless Chromium browser via pytest-playwright. That needs a one-time
# per-clone browser download (like `make gut` vendoring the GUT addon):
#   cd tools/map_editor && uv run playwright install chromium
map-editor-test-full:
	cd tools/map_editor && uv run --group dev pytest -q -m ""

map-editor-js-check:
	cd tools/map_editor && npm install --no-audit --no-fund --silent && npm run check

# Composites every layer in manifest order over the real backdrop and
# writes tools/map_editor/dev_map_data/preview.png. Runs the same
# validate_package the editor's Export button does, so an invalid polygon
# (self-intersecting, revisits a point, off-map, overlapping another
# province) gets outlined in red on the image and listed on stdout.
map-editor-preview:
	cd tools/map_editor && uv run preview.py

# Bulk-adds cities from a lon/lat gazetteer CSV, projecting each through the
# map's persisted georef (map.json) so they land on the coastline without
# any visual placement. Idempotent: re-running rebuilds the managed set
# (ids "ukc-*") and never touches hand-placed cities. Tune with MIN_POP / TOP.
# The CSV is licensed and gitignored - see add_cities.py for where to put it.
#   make map-editor-add-cities MIN_POP=30000
map-editor-add-cities:
	cd tools/map_editor && uv run add_cities.py $(if $(MIN_POP),--min-pop $(MIN_POP),) $(if $(TOP),--top $(TOP),) $(if $(DRY),--dry-run,)

# Creates a fresh map package from a backdrop image: manifest, layer
# configs, faction roster, a coastline traced from the line art, and a
# terrain layer seeded to plains. SEED=N also chops the land into N
# placeholder provinces so the game has something to run on before
# anything has been traced by hand.
#   make map-package-init SEED=12
map-package-init:
	cd tools/map_editor && uv run init_package.py $(if $(SEED),--seed-provinces $(SEED),) $(if $(FORCE),--force,)

# Loads the dev package and reports any problem that would block an
# export, without writing anything. CI-able.
map-package-check:
	cd tools/map_editor && uv run check_package.py

# Copies the dev map package into campaign/map_data/, where the game
# loads it from. Run this after editing layers in `make map-editor` and
# exporting, once you're happy with the result.
#
# Also forces a Godot reimport: Godot caches each layer PNG as a .ctex
# under .godot/imported/, keyed by content hash, and `make play` never
# triggers a reimport itself - overwriting the source PNGs alone leaves
# the game rendering stale cached textures, so newly-drawn provinces
# silently don't show up until this runs.
promote-map:
	@test -f tools/map_editor/dev_map_data/provinces.table.json \
		|| (echo "No exported dev package to promote - export from the map editor first (make map-editor)."; exit 1)
	cd tools/map_editor && uv run promote.py
	$(GODOT) --headless --import
	@echo "Promoted dev map package -> campaign/map_data/ and reimported"
