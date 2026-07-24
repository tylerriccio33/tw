"""Editor-scene helpers shared by the builders.

Everything the toolkit spawns is tagged ``tw`` (plus a per-layer tag), so a
rebuild is *diff-free by construction*: `clear(layer)` destroys exactly the
previous run's actors for that layer and nothing an artist placed by hand. This
is the same "rebuild from the snapshot, never accumulate" discipline the old C++
renderer enforced with ID-set diffing.
"""

from __future__ import annotations

from typing import Iterable

import unreal

TW_TAG = unreal.Name("tw")


def _actor_subsystem() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def spawn(
    actor_class: type,
    location: unreal.Vector | None = None,
    rotation: unreal.Rotator | None = None,
    *,
    layer: str,
    label: str | None = None,
) -> unreal.Actor:
    """Spawn an actor tagged for this toolkit and layer."""
    loc = location or unreal.Vector(0, 0, 0)
    rot = rotation or unreal.Rotator(0, 0, 0)
    actor = _actor_subsystem().spawn_actor_from_class(actor_class, loc, rot)
    actor.tags = [TW_TAG, unreal.Name(f"tw.{layer}")]
    if label:
        actor.set_actor_label(label)
    return actor


def clear(layer: str | None = None) -> int:
    """Destroy previously spawned tw actors. With ``layer``, only that layer;
    without, every tw actor. Returns the count removed."""
    want = unreal.Name(f"tw.{layer}") if layer else TW_TAG
    sub = _actor_subsystem()
    removed = 0
    for actor in sub.get_all_level_actors():
        if want in actor.tags:
            sub.destroy_actor(actor)
            removed += 1
    return removed


def clear_all() -> int:
    return clear(None)


def vec(xyz: Iterable[float]) -> unreal.Vector:
    """A 3-list from a bake JSON (already Unreal cm) -> unreal.Vector."""
    x, y, z = xyz
    return unreal.Vector(float(x), float(y), float(z))


CAMPAIGN_MAP_PATH = "/Game/Maps/Campaign"


def save_open_level() -> None:
    """Persist the current level — how the headless `twctl build` leaves a result
    on disk that a later `twctl shot` reopens.

    A fresh headless run opens the engine's untitled transient level, which has
    no package filename yet — `LevelEditorSubsystem.save_current_level()` fails
    silently in that case ("Can't save the level because it doesn't have a
    filename"). `save_map` writes to an explicit path regardless of whether the
    level already has one, so use it unconditionally instead of branching on
    asset-registry state (which can report an asset present before the level
    the caller sees actually has that filename).
    """
    world = unreal.EditorLevelLibrary.get_editor_world()
    unreal.EditorLoadingAndSavingUtils.save_map(world, CAMPAIGN_MAP_PATH)
