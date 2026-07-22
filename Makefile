# tw — Total War-style campaign game (Python simulation + Unreal renderer)
# `make help` lists everything.

SEED ?= 42
ROUNDS ?= 120
.PHONY: help fmt clippy clean bake \
        py-sim py-test py-server cpp-test unreal-build unreal-play unreal-test \
        unreal-shots unreal-shot unreal-live shots-diff shots-bless \
        pre-commit-install pre-commit

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

clippy: ## Lint the baker (warnings are errors)
	cargo clippy --all-targets -- -D warnings

fmt: ## Format the baker's Rust
	cargo fmt

clean: ## Remove build artifacts
	cargo clean

bake: ## Bake the map geometry into unreal/Content/Map/ (one-shot, offline)
	cargo run -p tw-bake

# --- the Python simulation (sim/) — the source of truth for all game rules ---

py-test: ## Run the Python simulation test suite
	cd sim && uv run pytest

py-sim: ## Watch an all-AI campaign (SEED=42, ROUNDS=120) — fastest balance check
	cd sim && uv run python -m tw_sim.cli --seed $(SEED) --rounds $(ROUNDS)

# Writes sim/.sim-port as well as printing the port, which is what lets an
# already-open Unreal editor attach instead of spawning its own: restart this
# and the editor picks the new sidecar up on the next campaign.
py-server: ## Run the simulation sidecar (prints and advertises the port it picked)
	cd sim && uv run python -m tw_sim.server --port-file .sim-port

# --- the Unreal bridge (unreal/Source/TotalWarlike/Sim) ---

# The engine-free half of the bridge — the msgpack codec, the framing and the
# province hit test — compiled with plain clang++ and run against a real
# sidecar. Seconds, versus a full editor build for the automation tests.
cpp-test: ## Test the C++ bridge without Unreal (needs clang++ and uv)
	@mkdir -p target/cpp
	clang++ -std=c++17 -Wall -Wextra -O1 -o target/cpp/wire_test \
		unreal/Tests/wire_test.cpp \
		unreal/Source/TotalWarlike/Sim/MsgPack.cpp \
		unreal/Source/TotalWarlike/Map/ProvinceLookup.cpp
	./target/cpp/wire_test sim

UE ?= /Users/Shared/Epic Games/UE_5.8

unreal-build: ## Compile the Unreal editor target (UE=<engine dir> to override)
	"$(UE)/Engine/Build/BatchFiles/Mac/Build.sh" TotalWarlikeEditor Mac Development \
		-project="$(CURDIR)/unreal/TotalWarlike.uproject" -waitmutex

# The Unreal vertical slice. There is no .umap: ACampaignGameMode spawns the sun,
# the sea and ACampaignMap at BeginPlay, so -game on a stock empty level is the
# whole launch. The sidecar starts itself (ESidecarMode::Auto) unless you already
# have `make py-server` running, in which case it attaches to that one — which is
# how you restart Python without closing the game.
unreal-play: unreal-build ## Play the Unreal campaign map (WASD pan, wheel zoom, click, Space)
	"$(UE)/Engine/Binaries/Mac/UnrealEditor" "$(CURDIR)/unreal/TotalWarlike.uproject" \
		-game -windowed -ResX=1600 -ResY=900

# --- the visual loop (see unreal/Shots/README.md) ---
#
# Editing how the game LOOKS is edit -> view -> iterate, and "view" has to be one
# command with a fixed camera or the iteration is not comparable. UShotDirector
# takes named preset shots headlessly and writes them to unreal/Shots/current/;
# unreal/Shots/golden/ is what they are compared against.

SHOTS ?= overview,lowlands,coast,mountain,border
SHOT_RES ?= 1600 900
comma := ,

# The `-` and the check afterwards are not laziness. UnrealEditor on macOS
# reliably SIGTRAPs inside FMacApplication's destructor on the way out of a
# windowed -game session, *after* every PNG has been written — an engine
# teardown bug we cannot fix and should not be blocked by. So the exit code is
# ignored and the actual contract, "every requested shot is on disk", is checked
# directly. If a shot is missing, this fails.
unreal-shots: unreal-build ## Render the preset campaign screenshots to unreal/Shots/current/
	@rm -f $(foreach s,$(subst $(comma), ,$(SHOTS)),unreal/Shots/current/$(s).png)
	-"$(UE)/Engine/Binaries/Mac/UnrealEditor" "$(CURDIR)/unreal/TotalWarlike.uproject" \
		-game -windowed -ResX=$(word 1,$(SHOT_RES)) -ResY=$(word 2,$(SHOT_RES)) -ForceRes \
		-TWShots=$(SHOTS) -TWShotDir="$(CURDIR)/unreal/Shots/current" \
		-nosplash -unattended -nopause
	@missing=""; for s in $(subst $(comma), ,$(SHOTS)); do \
		[ -f unreal/Shots/current/$$s.png ] || missing="$$missing $$s"; done; \
	if [ -n "$$missing" ]; then \
		echo "shots MISSING:$$missing (see ~/Library/Logs/TotalWarlike/TotalWarlike.log)"; exit 1; \
	fi; \
	echo "shots written to unreal/Shots/current/: $(SHOTS)"

# The live loop. Keeps ONE process up and drives it from the console, so a .ush
# edit costs a shader recompile (~2s) instead of a relaunch (~20s):
#   ~ (in the game window)  `recompileshaders changed`  then  TWView mountain
#                           TWShot mountain
# The console is the backtick key. -AllowConsoleInGame is what puts it there in
# a -game build, where it is off by default.
unreal-live: unreal-build ## Play with the console + shader hot-reload wired up (the tight visual loop)
	@echo "console: backtick. try:  TWShotsList | TWView mountain | recompileshaders changed | TWShot try1"
	"$(UE)/Engine/Binaries/Mac/UnrealEditor" "$(CURDIR)/unreal/TotalWarlike.uproject" \
		-game -windowed -ResX=1600 -ResY=900 \
		-AllowConsoleInGame -TWShotDir="$(CURDIR)/unreal/Shots/current"

# One preset, for a tight loop on a single thing: make unreal-shot SHOT=mountain
unreal-shot: ## Render a single preset (SHOT=mountain)
	$(MAKE) unreal-shots SHOTS=$(SHOT)

shots-diff: ## Compare unreal/Shots/current/ against golden/ and report what moved
	uv run --with pillow python unreal/Shots/diff.py

shots-bless: ## Accept unreal/Shots/current/ as the new golden set
	uv run --with pillow python unreal/Shots/diff.py --bless

# Runs every TotalWarlike automation test: .Sim spawns a real Python sidecar,
# .Map reads the baked Content/Map (and skips itself if it has not been baked).
#
# UnrealEditor-Cmd exits 0 whether the tests passed or failed — it reports the
# run, not the result — so a red suite used to look exactly like a green one from
# here. The results only exist in the log, so read them out of the log: print
# every verdict, then fail on any {Fail}. `Ran no tests` is also a failure; a
# filter that matches nothing must not read as success.
UNREAL_TEST_LOG = $(CURDIR)/unreal/Saved/automation.log
unreal-test: unreal-build ## Run the Unreal-side automation tests headless
	@mkdir -p $(dir $(UNREAL_TEST_LOG))
	@"$(UE)/Engine/Binaries/Mac/UnrealEditor-Cmd" "$(CURDIR)/unreal/TotalWarlike.uproject" \
		-ExecCmds="Automation RunTests TotalWarlike" \
		-unattended -nullrhi -nosplash -nopause -stdout -FullStdOutLogOutput \
		-testexit="Automation Test Queue Empty" > $(UNREAL_TEST_LOG) 2>&1 || true
	@sed -n 's/.*Test Completed\. Result={\([^}]*\)} Name={\([^}]*\)}.*/  \1  \2/p' \
		$(UNREAL_TEST_LOG) || true
	@if grep -q "Result={Fail}" $(UNREAL_TEST_LOG); then \
		echo "unreal-test FAILED — full log: $(UNREAL_TEST_LOG)"; \
		sed -n 's/.*LogAutomationController: Error: /  /p' $(UNREAL_TEST_LOG); \
		exit 1; \
	elif ! grep -q "Test Completed" $(UNREAL_TEST_LOG); then \
		echo "unreal-test ran no tests — full log: $(UNREAL_TEST_LOG)"; exit 1; \
	else \
		echo "unreal-test passed"; \
	fi

# --- pre-commit (prek) — the local CI gate; see CLAUDE.md ---

pre-commit-install: ## Install the prek git hook (one-time per clone)
	uvx prek install

pre-commit: ## Run all prek hooks against the whole repo
	uvx prek run --all-files
