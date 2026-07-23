"""Headless entry point: (re)build the world if needed, render presets, quit.

Run by `twctl shot [names...]`. ``TW_SHOTS`` is a comma-separated preset list
(empty = all). ``TW_BUILD=1`` forces a fresh world build first; otherwise it
builds only if the level looks empty of tw actors, so a `twctl build` followed by
several `twctl shot` calls does not rebuild every time.
"""

import os

import unreal

import tw.presets
import tw.render
import tw.world
from tw import _scene


def _needs_build() -> bool:
    if os.environ.get("TW_BUILD") == "1":
        return True
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    return not any(_scene.TW_TAG in a.tags for a in sub.get_all_level_actors())


if _needs_build():
    tw.world.build_world(
        campaign=os.environ.get("TW_CAMPAIGN", "britain"),
        seed=int(os.environ.get("TW_SEED", "42")),
    )

names = [n for n in os.environ.get("TW_SHOTS", "").split(",") if n]
written = tw.render.shoot(names or None)
unreal.log(f"[tw] rendered {len(written)} shots")
unreal.SystemLibrary.quit_editor()
