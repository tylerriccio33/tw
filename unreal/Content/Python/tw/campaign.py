"""Snapshot -> scene, and (slice 2) input -> command.

Slice 1 is read-only: `apply_snapshot` rebuilds just the ownership-dependent
layers (borders, markers) from a fresh snapshot, leaving the static geography and
lighting untouched — the same "only ownership layers rebuild" split the old
renderer used so armies glide instead of blinking.

Slice 2 (deferred) adds interactivity here: pan/zoom/select and end-turn become
commands sent through `simbridge`, the snapshot comes back, and this function
repaints. The frontend still contains no rules — legality is discovered by
sending the command and letting the sim refuse it.
"""

from __future__ import annotations

import unreal

from . import borders, markers, simbridge


def apply_snapshot(snapshot: dict) -> None:
    """Repaint the ownership layers from a snapshot."""
    borders.build(snapshot)
    markers.build(snapshot)
    unreal.log(f"[tw] applied snapshot: turn {snapshot.get('turn')}")


def end_turn() -> dict:
    """Resolve a turn through the sim and repaint. Returns the new snapshot."""
    with simbridge.Sim() as sim:
        result = sim.end_turn()
    apply_snapshot(result["snapshot"])
    return result["snapshot"]
