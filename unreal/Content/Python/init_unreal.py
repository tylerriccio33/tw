"""Editor startup hook — Unreal runs this automatically when the Python plugin
boots (both in the interactive editor and under `-run=pythonscript`).

`Content/Python` is already on `sys.path`, so `import tw` just works. All this
does is make that explicit, surface the toolkit in the log, and register a couple
of console commands so a human in a live session has the same entry points the
`twctl` CLI drives headlessly.
"""

import os

import unreal

import tw

unreal.log(f"[tw] toolkit loaded from {tw.__file__}")
unreal.log("[tw] build: tw.world.build_world()  |  shots: tw.render.shoot([...])")

# The tight loop's transport. `twctl live` sets TW_EXEC_SERVER=1 so a persistent
# editor opens a loopback TCP eval server (see exec_server.py) that `twctl exec`
# talks to — no UDP multicast, which macOS's Local Network gate silently drops.
# Guarded on the env var because this hook also runs inside every headless
# `-run=pythonscript` commandlet (build/shot/assets), which must not open sockets.
if os.environ.get("TW_EXEC_SERVER"):
    import exec_server

    exec_server.start()
