  The big one: Godot lies about success

  The very first thing I ran exited 0 on a parse error. Later, a runtime error aborted terrain
  generation mid-function and the harness cheerfully printed SHOT OK and wrote three screenshots
  of a broken world. GDScript unwinds only the failing function and returns to the caller, so an
  empty error list means nothing.

  I patched it with a built completion flag on every stage — but nothing enforces that. Any stage
  I forget to guard reopens the hole silently. The systemic fix is a wrapper that scans Godot's
  stderr for SCRIPT ERROR/ERROR: and fails the target regardless of exit code. That single change
  would have saved me three wasted cycles and one nearly-accepted broken baseline.

  make accept will happily bless a bug

  I promoted a golden containing both the black band and the water TIME nondeterminism. The
  determinism check is the thing that catches this, and it's currently a manual step I have to
  remember. It should be part of accept: render twice, refuse to promote if they differ. That
  would have caught the TIME bug at the moment I introduced it rather than four milestones later.

  I read three images when I wanted one

  Image reads are my most expensive operation, and every iteration I read overview, coast, ridge
  separately. The contact sheet only works against a golden — useless while the scene is changing
  shape every render. I want make sheet: tile the current presets into one labelled image. Cuts my
  per-iteration read cost by 3×.

  I was flying blind on counts

  Nothing told me how many trees, cities, rivers, or roads were actually emitted unless I'd
  hand-written a check. Printing a stats block every render — region count, height range,
  tree/city/river/road counts, triangle count — is nearly free and would have made "roads are
  zigzagging" and "cities placed 6 of 6" visible without looking at a picture at all.

  Terrain regen dominates every render

  ~7s of each 10s cycle rebuilds terrain, even when I'm only tuning a wall rotation. Terrain3D has
  save_directory — caching the generated regions keyed by a hash of the terrain config + seed
  would cut iteration to ~3s for every non-terrain change. That's the difference between 10
  experiments and 30 in the same time.

  I mutated a committed config to run experiments

  Every debug-view toggle was a python3 -c one-liner rewriting world.json. I left it set to
  "control" once and rendered a garbage image. Wanted: make shot SET=debug.terrain_view=grey as a
  transient override that never touches the file.

  Typed-inference errors cost a full render each

  Three times I lost a 10s cycle to var x := some_untyped_call() — warning-as-error, only surfaced
  at runtime. A make check that just parses the scripts would catch those in about a second.

  Two silent Terrain3D footguns

  terrain.material and terrain.region_size are both silently dropped if set before the node enters
  the tree. The region_size one was especially nasty — it fell back to the 256 default and
  quietly produced 64 regions instead of 4. make api (introspection) was the tool that saved me
  repeatedly; I should have reached for it before trusting docs.

  No tests on pure logic

  MST, ribbon extrusion, Voronoi partition, height generation are all pure and headlessly testable
  in under a second. The road-wander bug — 35 noise cycles per road instead of ~1 — is exactly
  the kind of thing a bounded-curvature assertion catches instantly, and instead I found it by eye
  three renders later.

  ---
  My recommendation: the top four (stderr gate, determinism-in-accept, make sheet, stats block)
  are maybe an hour of work and remove entire categories of wasted cycles. Terrain caching is the
  biggest raw speedup. Tests I'd add only around the pure geometry helpers, not the visuals.

  Want me to build those? I'd suggest the first four plus caching, and skip the rest until they
  bite again.
