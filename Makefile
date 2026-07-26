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

.PHONY: help addons import shot diff sheet accept smoke api check test clean-shots

ci: ## Commit on a fresh branch, push, open a PR into main, and auto-merge it
	@if [ "$$(git rev-parse --abbrev-ref HEAD)" = "main" ]; then \
		branch="ci/$$(date +%Y%m%d%H%M%S)"; \
		echo "On main - creating branch $$branch"; \
		git checkout -b "$$branch"; \
	fi
	@echo "Staging everything"
	@git add .
	@echo "Running pre-commit"
	@uvx prek run --all-files
	@echo "Committing with message: $(MSG)"
	@git commit -m "$(MSG)"
	@echo "Pushing to origin"
	@git push -u origin HEAD
	@echo "Opening PR via CLI with title: $(TITLE)"
	@gh pr create --title "$(TITLE)" --body "Auto-generated PR from CI" --base main --head "$$(git rev-parse --abbrev-ref HEAD)"
	@echo "Merging PR on main"
	@gh pr merge --merge --delete-branch
	@echo "Done"

help:
	@echo "make addons   - vendor the Terrain3D GDExtension into addons/"
	@echo "make import   - reimport assets and register the GDExtension"
	@echo "make shot     - render every preset in config/shots.json to shots/current/"
	@echo "make diff     - PSNR table + shots/contact_sheet.png vs shots/golden/"
	@echo "make sheet    - tile shots/current/ presets into shots/sheet.png (no golden needed)"
	@echo "make accept   - render twice, then promote shots/current/ to shots/golden/ (refuses to promote if the two renders differ)"
	@echo "make smoke    - verify the Terrain3D extension loads and its API is present"
	@echo "make api      - dump the Terrain3D API surface (properties, methods, enums)"
	@echo "make check    - parse every .gd file for errors (incl. type-inference warnings) in ~1s, no render"
	@echo "make test     - headless tests for MST/ribbon/Voronoi/road-wander/height-generation"
	@echo "make shot SET=debug.terrain_view=grey - transient config override, world.json untouched"

addons:
	python3 tools/fetch_addons.py

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

# Renders twice and diffs the two renders byte-for-byte before promoting.
# An unseeded input (e.g. water's wave_time keyed off real elapsed time
# instead of the config) makes consecutive renders of an unchanged config
# differ; without this check that nondeterminism gets baked into golden and
# every future `make diff` shows permanent, unexplained drift.
accept:
	@rm -rf /tmp/tw_accept_check
	@$(MAKE) --no-print-directory shot
	@mkdir -p /tmp/tw_accept_check
	@cp shots/current/*.png /tmp/tw_accept_check/
	@$(MAKE) --no-print-directory shot
	@if ! diff -rq shots/current /tmp/tw_accept_check; then \
		echo "error: two consecutive renders of the same config differ - refusing to promote."; \
		echo "This means something is reading an unseeded input (wall clock, uninitialized RNG, etc)."; \
		rm -rf /tmp/tw_accept_check; \
		exit 1; \
	fi
	@rm -rf /tmp/tw_accept_check
	@mkdir -p shots/golden
	@cp shots/current/*.png shots/golden/
	@echo "renders were deterministic; promoted $$(ls shots/current/*.png | wc -l | tr -d ' ') shot(s) to shots/golden/"

# --check-only parses a script and reports errors, but still exits 0 on a
# parse error - the same lie make shot works around, so this goes through
# godot_gate.sh too. Combined with treat_warnings_as_errors in project.godot,
# `var x := some_untyped_call()` now fails here in about a second instead of
# surfacing mid-render, three stages deep, ten seconds later.
check:
	@fail=0; \
	for f in world/*.gd tools/*.gd; do \
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

clean-shots:
	rm -rf shots/current shots/contact_sheet.png shots/sheet.png
