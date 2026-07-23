"""Headless entry point: build the whole world and save the level, then quit.

Run by `twctl build` via `UnrealEditor-Cmd -run=pythonscript -script=.../build.py`.
Parameters arrive as environment variables so they survive Unreal's arg parsing:
``TW_CAMPAIGN`` (default britain), ``TW_SEED`` (default 42).
"""

import os

import unreal

import tw.world

tw.world.build_world(
    campaign=os.environ.get("TW_CAMPAIGN", "britain"),
    seed=int(os.environ.get("TW_SEED", "42")),
    save=True,
)
unreal.SystemLibrary.quit_editor()
