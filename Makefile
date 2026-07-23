# tw — Total War-style campaign game (Python simulation + Python-driven Unreal).
# `make help` lists everything.

SEED ?= 42
ROUNDS ?= 120
# twctl is stdlib-only, so it runs straight from source with no install/venv.
TWCTL = PYTHONPATH=tools/twctl/src python3 -m twctl

.PHONY: help fmt clippy clean bake \
        py-test py-sim sim \
        build shot shots-diff shots-bless live exec assets bridge-test \
        pre-commit-install pre-commit

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

# --- the Rust geometry baker (bake/) — one-shot, offline ---

clippy: ## Lint the baker (warnings are errors)
	cargo clippy --all-targets -- -D warnings

fmt: ## Format the baker's Rust
	cargo fmt

clean: ## Remove build artifacts
	cargo clean

bake: ## Bake map geometry into unreal/Content/Map/ (heightmap, borders, rivers, forests, provinces)
	cargo run -p tw-bake

# --- the Python simulation (sim/) — the source of truth for all game rules ---

py-test: ## Run the Python simulation test suite (the gate)
	cd sim && uv run pytest

py-sim: ## Watch an all-AI campaign (SEED=42, ROUNDS=120) — fastest balance check
	cd sim && uv run python -m tw_sim.cli --seed $(SEED) --rounds $(ROUNDS)

sim: ## Run the simulation sidecar (writes sim/.sim-port so a live editor attaches)
	$(TWCTL) sim

# --- the Unreal frontend, driven entirely through the Python API (twctl) ---
#
# There is NO C++ module. Every actor, material, light and screenshot is built by
# tw-package Python inside the editor. twctl launches the editor headlessly for
# these, guarding free disk space (a full launch has twice wedged this machine).

build: ## Headless: build + save the whole campaign world from the bake + a snapshot
	$(TWCTL) build --seed $(SEED)

# The visual loop: render fixed-camera presets, then look at and diff the PNGs.
# SHOTS overrides the set, e.g. make shot SHOTS="mountain border".
SHOTS ?=
shot: ## Headless: render preset shots to unreal/Shots/current/ (SHOTS="mountain border")
	$(TWCTL) shot $(SHOTS)

shots-diff: ## Compare unreal/Shots/current/ against golden/ and report what moved
	$(TWCTL) diff

shots-bless: ## Accept unreal/Shots/current/ as the new golden set
	$(TWCTL) bless

assets: ## Headless: (re)build the code-owned materials (assets-as-code)
	$(TWCTL) assets

# The tight loop: one persistent editor, driven from another shell over remote
# execution. In window A: `make live`. In window B: `make exec CODE=...`.
live: ## Launch a persistent editor with Python remote-execution on (the tight loop host)
	$(TWCTL) live

CODE ?=
exec: ## Push Python into the live editor (CODE='import tw.materials; tw.materials.terrain.build()')
	@test -n "$(CODE)" || { echo "set CODE=<file or snippet>"; exit 1; }
	$(TWCTL) exec "$(CODE)"

# --- the bridge test — the fast gate for the pure-Python sim bridge ---
#
# The role the old `make cpp-test` played: exercise the whole wire protocol
# against a real sidecar in seconds, no editor. Also cross-checks the vendored
# msgpack codec against the reference library.
bridge-test: ## Test the pure-Python sim bridge + msgpack codec against a real sidecar
	cd sim && uv run python ../unreal/Content/Python/tests/test_bridge.py

# --- pre-commit (prek) — the local CI gate ---

pre-commit-install: ## Install the prek git hook (one-time per clone)
	uvx prek install

pre-commit: ## Run all prek hooks against the whole repo
	uvx prek run --all-files
