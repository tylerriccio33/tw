GODOT ?= godot

# `make shot SET=debug.terrain_view=grey` (dot-path=value, comma-separated for
# several) patches config/world.json in memory for this render only - the
# file on disk is never touched, so there is no risk of a transient debug
# view leaking into a later golden render.
ifneq ($(SET),)
SHOT_ARGS := -- --set=$(SET)
else
SHOT_ARGS :=
endif

.PHONY: help addons textures foliage import shot diff sheet accept smoke api check test clean-shots campaign campaign-test campaign-smoke play play-shot hud-shot

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
	@echo "make addons   - vendor the Terrain3D GDExtension into addons/"
	@echo "make textures - vendor ambientCG PBR ground textures into assets/textures/"
	@echo "make foliage  - vendor Poly Haven CC0 tree textures into assets/foliage/"
	@echo "make import   - reimport assets and register the GDExtension"
	@echo "make shot     - render every preset in config/shots.json to shots/current/"
	@echo "make diff     - PSNR table + shots/contact_sheet.png vs shots/golden/"
	@echo "make sheet    - tile shots/current/ presets into shots/sheet.png (no golden needed)"
	@echo "make accept   - render twice, then promote shots/current/ to shots/golden/ (refuses if the two renders disagree by more than ACCEPT_MIN_PSNR dB)"
	@echo "make smoke    - verify the Terrain3D extension loads and its API is present"
	@echo "make api      - dump the Terrain3D API surface (properties, methods, enums)"
	@echo "make check    - parse every .gd file for errors (incl. type-inference warnings) in ~1s, no render"
	@echo "make test     - headless tests for MST/ribbon/Voronoi/road-wander/height-generation"
	@echo "make shot SET=debug.terrain_view=grey - transient config override, world.json untouched"
	@echo "make campaign      - build the campaign-map Rust GDExtension and install it into addons/campaign/bin/"
	@echo "make campaign-test - run the Rust unit tests for the campaign-map game logic"
	@echo "make play          - build the extension and open the campaign map in a window"
	@echo "make play-shot     - screenshot the whole campaign window to shots/play/play.png"
	@echo "make hud-shot      - screenshot just the bottom HUD banner to shots/play/hud.png"

addons:
	python3 tools/fetch_addons.py

textures:
	@uv run tools/fetch_textures.py

# Textures only, not meshes: Poly Haven's photoscanned trees are 1-7M triangles
# each and scatter.gd places ~4500 of them. See tools/fetch_foliage.py.
foliage:
	@uv run tools/fetch_foliage.py

# Godot only registers a GDExtension after a project import, so a fresh clone
# renders an empty scene until this has run at least once.
import:
	$(GODOT) --headless --import

smoke:
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless --script res://tools/smoke.gd

api:
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless --script res://tools/introspect.gd

# Runs windowed on purpose: Godot cannot capture a viewport in --headless mode,
# there is no display server to render with. The window is 128x128 (see
# project.godot) while capture happens through a full-resolution SubViewport.
shot:
	@rm -f shots/current/*.png
	GODOT=$(GODOT) ./tools/godot_gate.sh $(SHOT_ARGS)

diff:
	@uv run tools/diff_shots.py

sheet:
	@uv run tools/make_sheet.py

# Renders twice and requires the two runs to agree to within ACCEPT_MIN_PSNR
# before promoting. An unseeded input (e.g. water's wave_time keyed off real
# elapsed time instead of the config) makes consecutive renders of an unchanged
# config differ; without this check that nondeterminism gets baked into golden
# and every future `make diff` shows permanent, unexplained drift.
#
# This used to demand byte-for-byte equality, which was strictly better. TAA,
# SDFGI and volumetric fog are all temporally accumulated and do not converge
# bit-exactly in a fixed frame budget, so the strict form started failing on
# correct renders. The PSNR floor still craters on a real unseeded input (which
# moves whole regions of the frame) while tolerating temporal jitter. Raise
# ACCEPT_MIN_PSNR if that tolerance ever starts hiding something.
ACCEPT_MIN_PSNR ?= 45

accept:
	@rm -rf /tmp/tw_accept_check
	@$(MAKE) --no-print-directory shot
	@mkdir -p /tmp/tw_accept_check
	@cp shots/current/*.png /tmp/tw_accept_check/
	@$(MAKE) --no-print-directory shot
	@uv run tools/diff_shots.py --compare shots/current /tmp/tw_accept_check \
		--min-psnr $(ACCEPT_MIN_PSNR) || { rm -rf /tmp/tw_accept_check; exit 1; }
	@rm -rf /tmp/tw_accept_check
	@mkdir -p shots/golden
	@cp shots/current/*.png shots/golden/
	@echo "renders agreed within the PSNR floor; promoted $$(ls shots/current/*.png | wc -l | tr -d ' ') shot(s) to shots/golden/"

# --check-only parses a script and reports errors, but still exits 0 on a
# parse error - the same lie make shot works around, so this goes through
# godot_gate.sh too. Combined with treat_warnings_as_errors in project.godot,
# `var x := some_untyped_call()` now fails here in about a second instead of
# surfacing mid-render, three stages deep, ten seconds later.
check:
	@fail=0; \
	for f in world/*.gd tools/*.gd campaign/*.gd; do \
		echo "checking $$f"; \
		if ! GODOT=$(GODOT) ./tools/godot_gate.sh --headless --check-only --script "res://$$f"; then \
			echo "FAIL: $$f"; \
			fail=1; \
		fi; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "check OK"

test:
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless --script res://tools/tests.gd

# Builds the campaign-map GDExtension in release mode and installs the dylib
# where campaign.gdextension expects it. Re-run after any rust/campaign/ change.
campaign:
	cd rust/campaign && cargo build --release
	@mkdir -p addons/campaign/bin
	cp rust/campaign/target/release/libcampaign.dylib addons/campaign/bin/libcampaign.dylib

campaign-test:
	cd rust/campaign && cargo test

# Opens the campaign map in a real window, bypassing project.godot's
# run/main_scene (tools/shoot.tscn) for this one launch only. project.godot
# pins the window to 128x128 for shoot.gd's benefit; --resolution overrides
# that just for this launch without touching the file.
play: campaign
	$(GODOT) res://campaign/campaign.tscn --resolution $(or $(RESOLUTION),1280x800)

# Launches `make play` windowed, waits for it to settle, and takes an
# OS-level screenshot of the window (there's no in-game SubViewport capture
# path like shoot.gd has, since this is live gameplay, not a config-driven
# render) to shots/play/play.png. macOS only.
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

campaign-smoke:
	GODOT=$(GODOT) ./tools/godot_gate.sh --headless --script res://tools/campaign_smoke.gd

clean-shots:
	rm -rf shots/current shots/contact_sheet.png shots/sheet.png
