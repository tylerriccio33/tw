"""Headless entry point: (re)build the assets-as-code materials, then quit.

Run by `twctl assets`. This is the blessed way any authored .uasset is created —
as a re-runnable Python graph, never hand-authored — so a look change to a
material lands as a reviewable diff.
"""

import unreal

import tw.materials

tw.materials.build_all()
unreal.log("[tw] assets built")
unreal.SystemLibrary.quit_editor()
