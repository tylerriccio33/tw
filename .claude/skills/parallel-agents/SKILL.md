---
name: parallel-agents
description: Fan a multi-part request out to several agents running in parallel, when the parts naturally touch disjoint modules/files. Use whenever a request lists two or more independent changes (e.g. "do X, Y, and Z" / "in parallel") that don't obviously share the same files. Covers when to skip worktree isolation entirely, how each agent should test only its own slice, and the final integration pass.
---

Workflow for splitting an incoming request into independent chunks and running them concurrently, without the coordination overhead of isolated worktrees unless it's actually needed.

## When this applies

The prompter is asking for several things that are naturally parallel: each one is scoped to a different module, file, or layer of the codebase (e.g. one change lives entirely in `rust/campaign/src/model.rs`, another entirely in `campaign/campaign_ui.gd`'s click handling, a third in the map editor). If the parts are likely to touch the *same* file/function, this isn't a good fit — do it sequentially yourself or in one agent instead of parallelizing.

## Step 1: partition the request

Before spawning anything, read the request and mentally assign each sub-task to the file(s)/function(s) it will touch (use Explore or a quick grep if unsure). Two sub-tasks are safe to parallelize when their edit surfaces don't overlap — different files, or clearly different functions in the same file. If two sub-tasks look like they'll edit the *same function*, merge them into one agent's task instead of splitting.

Keep this partitioning pass cheap and treat its findings as a rough starting map, not verified fact — don't spend a full separate Explore agent round-trip on it for a request this size, and don't feed its line numbers to fix-agents as if they were load-bearing. By the time a fix-agent actually opens the file the numbers may have drifted (earlier edits, or another agent's change), and a fix-agent should always re-read and re-verify the relevant code itself before trusting a diagnosis handed to it — including whether the described bug is even still present.

## Step 2: decide whether you need worktree isolation at all

Default to **no isolation** — let agents edit the shared working tree directly — when you're confident their edit surfaces are disjoint (per Step 1). This is cheaper and avoids a merge step entirely.

Only pass `isolation: "worktree"` when:
- You're not fully sure the edit surfaces stay disjoint (e.g. both touch the same file and you can't rule out overlapping hunks), or
- The task is risky/exploratory enough that you want a clean rollback option.

If you do use worktrees, **you must delete them once merged** — see Step 4. Don't leave `.claude/worktrees/agent-*` directories or their branches lying around after the work is integrated; that's it's own cleanup debt in every subsequent `git status`.

## Step 3: brief each agent to test only its own slice

Each agent's prompt should tell it to verify its own change with the *narrowest* relevant check, not the full CI suite — that keeps each agent fast and avoids redundant work when three agents each run the whole test suite for a one-file change:

- Rust-only change: `cargo test --manifest-path rust/campaign/Cargo.toml <specific_test_or_module>`, plus `make campaign`/`make campaign-smoke` if the change is behavioral.
- GDScript-only change: `make check` (parses fast, catches typed-inference errors), plus a targeted `make gut-test` run if relevant unit tests exist for the touched file.
- Map editor change: `cd tools/map_editor && uv run pytest -q <path::test_name>`.

Tell the agent explicitly **not** to run `make ci` / the full integration suite — that happens once, centrally, after all agents land (Step 5). Running it per-agent is redundant and, if agents share a tree, can race.

If an agent adds new GDScript test functions, tell it to also run `gdlint` on just the file(s) it touched (not the full suite) before reporting done, and to check the new function count against `.gdlintrc`'s `max-public-methods` (currently 30) with e.g. `grep -c '^func test_' <file>`. Catching an over-the-limit test file at this stage costs one lint call; catching it at Step 5 costs a full `make ci` cycle (Rust tests + GUT suite + render golden-image gate) per fix.

## Step 4: collect and merge

- **No isolation used**: agents already edited the shared tree — nothing to merge, just review `git diff` once all are done to sanity-check there was no unexpected overlap.
- **Worktree isolation used**: for each agent, `git diff` (or `git -C <worktree> diff`) to a patch file, `git apply --check` it against the main tree to confirm no conflicts, then `git apply` it for real. Once every patch is applied and reviewed, remove the worktrees:
  ```
  git worktree remove <path>          # or --force if it has the applied-and-now-redundant changes
  git branch -D <worktree-branch>
  ```
  Do this cleanup before ending your turn — don't leave it for later.

## Step 5: one integration pass

After all sub-tasks are merged into the working tree, run the real gate once: use the `integrate` skill (`make ci MSG="..."`) or at minimum the specific `make check` / `make campaign-test` / `make gut-test` combination relevant to everything touched. This is the only point where the full suite runs — catches cross-module interactions (e.g. a Rust signal shape change vs. the GDScript that reads it) that no single agent's narrow test would have seen.

## Notes

- If an agent's own report is confusing or reads like it didn't finish (e.g. it says "waiting on X" as its final result), don't trust the summary — go check its actual `git diff` yourself before deciding the task is done.
- Task-completion notifications can arrive more than once for the same agent; treat the diff on disk as ground truth, not the notification text.
