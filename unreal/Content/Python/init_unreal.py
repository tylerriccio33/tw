"""Editor startup hook — Unreal runs this automatically when the Python plugin
boots (both in the interactive editor and under `-run=pythonscript`).

`Content/Python` is already on `sys.path`, so `import tw` just works. All this
does is make that explicit, surface the toolkit in the log, and register a couple
of console commands so a human in a live session has the same entry points the
`twctl` CLI drives headlessly.
"""

import unreal

import tw

unreal.log(f"[tw] toolkit loaded from {tw.__file__}")
unreal.log("[tw] build: tw.world.build_world()  |  shots: tw.render.shoot([...])")
