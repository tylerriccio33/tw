# tw — Total War-style campaign game (Python simulation + Unreal renderer)
# `make help` lists everything.

SEED ?= 42
ROUNDS ?= 120
.PHONY: help fmt clippy clean bake \
        py-sim py-test py-server cpp-test unreal-build unreal-play unreal-test \
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

# Runs the TotalWarlike.Sim automation tests, which spawn a real Python sidecar.
unreal-test: unreal-build ## Run the Unreal-side bridge tests headless
	"$(UE)/Engine/Binaries/Mac/UnrealEditor-Cmd" "$(CURDIR)/unreal/TotalWarlike.uproject" \
		-ExecCmds="Automation RunTests TotalWarlike.Sim" \
		-unattended -nullrhi -nosplash -nopause \
		-testexit="Automation Test Queue Empty"

# --- pre-commit (prek) — the local CI gate; see CLAUDE.md ---

pre-commit-install: ## Install the prek git hook (one-time per clone)
	uvx prek install

pre-commit: ## Run all prek hooks against the whole repo
	uvx prek run --all-files
