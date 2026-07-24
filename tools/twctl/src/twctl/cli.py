"""twctl command dispatch.

    twctl sim                     run the tw_sim sidecar (writes sim/.sim-port)
    twctl build [--seed N]        headless: build + save the world
    twctl shot [names...]         headless: render preset shots to Shots/current
    twctl diff | bless            compare / accept the golden shots
    twctl assets                  headless: (re)build the code-owned materials
    twctl live                    launch a persistent editor (exec server on)
    twctl exec <file|snippet>     push Python into the live editor (tight loop)
    twctl kill                    stop the live editor started by `twctl live`

The editor is found at $TW_UE (default the memory's UE_5.8 path). Every editor
launch is guarded by a disk-space check and, for long-running ones, killed if the
volume drops toward full — a hazard that has twice wedged this machine.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
UPROJECT = REPO / "unreal" / "TotalWarlike.uproject"
PYDIR = REPO / "unreal" / "Content" / "Python"
SIM_DIR = REPO / "sim"
SHOTS_DIFF = REPO / "unreal" / "Shots" / "diff.py"

# Where the live editor's exec server publishes its loopback port + pid. Must
# match exec_server.PORT_FILE.
EXEC_PORT_FILE = REPO / "unreal" / ".exec-port"

UE = Path(os.environ.get("TW_UE", "/Users/Shared/Epic Games/UE_5.8"))
EDITOR_CMD = UE / "Engine" / "Binaries" / "Mac" / "UnrealEditor-Cmd"
EDITOR = UE / "Engine" / "Binaries" / "Mac" / "UnrealEditor"

MIN_FREE_GB = 3.0


# --------------------------------------------------------------------------- #
# disk guard
# --------------------------------------------------------------------------- #
def _free_gb(path: str = "/") -> float:
    return shutil.disk_usage(path).free / 1e9


def _require_disk() -> None:
    free = _free_gb()
    if free < MIN_FREE_GB + 1.0:
        sys.exit(
            f"only {free:.1f} GB free (< {MIN_FREE_GB + 1.0:.0f} GB) — refusing to "
            "launch the editor; free space first (this has wedged the machine)."
        )


def _run_editor_headless(script: Path, env: dict[str, str], timeout: int = 900) -> int:
    """Run a Python entry script under UnrealEditor-Cmd, watching free space and
    killing the process if it drops below the floor."""
    _require_disk()
    if not EDITOR_CMD.exists():
        sys.exit(f"no editor at {EDITOR_CMD} — set TW_UE to your engine dir")
    cmd = [
        str(EDITOR_CMD),
        str(UPROJECT),
        "-run=pythonscript",
        f"-script={script}",
        "-unattended",
        "-nosplash",
        "-nopause",
        "-stdout",
        "-FullStdOutLogOutput",
        # A commandlet has no RHI/scene by default (FApp::CanEverRender() is
        # false), so anything GPU-backed — render targets, scene captures,
        # the shot pipeline — silently no-ops or crashes without this.
        "-AllowCommandletRendering",
    ]
    print(f"[twctl] {script.name}  (free {_free_gb():.1f} GB)")
    proc = subprocess.Popen(cmd, env={**os.environ, **env})
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(2.0):
            if _free_gb() < MIN_FREE_GB:
                print(f"[twctl] free space < {MIN_FREE_GB} GB — killing editor")
                proc.send_signal(signal.SIGKILL)
                return

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
    finally:
        stop.set()
    return rc


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_sim(_a: argparse.Namespace) -> int:
    """Run the sidecar in the foreground (Ctrl-C to stop)."""
    return subprocess.call(
        ["uv", "run", "python", "-m", "tw_sim.server", "--port-file", ".sim-port"],
        cwd=SIM_DIR,
    )


def cmd_build(a: argparse.Namespace) -> int:
    env = {"TW_CAMPAIGN": a.campaign, "TW_SEED": str(a.seed)}
    rc = _run_editor_headless(PYDIR / "entry" / "build.py", env)
    print("[twctl] build ok" if rc == 0 else f"[twctl] build failed rc={rc}")
    return rc


def cmd_shot(a: argparse.Namespace) -> int:
    env = {
        "TW_CAMPAIGN": a.campaign,
        "TW_SEED": str(a.seed),
        "TW_SHOTS": ",".join(a.names),
        "TW_BUILD": "1" if a.build else "0",
    }
    current = REPO / "unreal" / "Shots" / "current"
    wanted = a.names or _preset_names()
    for s in wanted:
        (current / f"{s}.png").unlink(missing_ok=True)  # a missing shot must fail
    _run_editor_headless(PYDIR / "entry" / "shot.py", env)
    # The contract is "every requested shot is on disk", checked directly, because
    # UnrealEditor can SIGTRAP in teardown *after* writing every PNG on macOS — so
    # its exit code is not the signal; the files are.
    gone = [s for s in wanted if not (current / f"{s}.png").exists()]
    if gone:
        print(f"[twctl] shots MISSING: {gone} (see the editor log)")
        return 1
    print(f"[twctl] shots ok: {', '.join(wanted)}")
    return 0


def _preset_names() -> list[str]:
    """The preset names, read without importing the (unreal-dependent) tw package."""
    return ["overview", "lowlands", "coast", "mountain", "border"]


def cmd_assets(_a: argparse.Namespace) -> int:
    return _run_editor_headless(PYDIR / "entry" / "assets.py", {})


def cmd_diff(_a: argparse.Namespace) -> int:
    return subprocess.call(
        ["uv", "run", "--with", "pillow", "python", str(SHOTS_DIFF)]
    )


def cmd_bless(_a: argparse.Namespace) -> int:
    return subprocess.call(
        ["uv", "run", "--with", "pillow", "python", str(SHOTS_DIFF), "--bless"]
    )


def cmd_live(_a: argparse.Namespace) -> int:
    """Launch a persistent editor hosting the tight-loop exec server.

    `TW_EXEC_SERVER=1` makes the editor's startup hook open a loopback TCP eval
    server (see `unreal/Content/Python/exec_server.py`) and publish its port to
    `.exec-port`. `twctl exec` connects to that — no UDP multicast, which macOS's
    Local Network privacy gate silently drops, breaking Epic's remote execution.
    Left in the foreground; drive it with `twctl exec` from another shell."""
    _require_disk()
    if not EDITOR.exists():
        sys.exit(f"no editor at {EDITOR} — set TW_UE to your engine dir")
    EXEC_PORT_FILE.unlink(missing_ok=True)  # stale file must not shadow a fresh launch
    print("[twctl] launching live editor; drive it with `twctl exec ...`")
    env = {**os.environ, "TW_EXEC_SERVER": "1"}
    return subprocess.call([str(EDITOR), str(UPROJECT)], env=env)


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    chunks: list[bytes] = []
    while n > 0:
        chunk = sock.recv(n)
        if not chunk:
            return None
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def _read_exec_port() -> int | None:
    """The live editor's exec port, waiting briefly for a just-launched one."""
    for _ in range(20):
        if EXEC_PORT_FILE.exists():
            lines = EXEC_PORT_FILE.read_text().splitlines()
            if lines:
                try:
                    return int(lines[0])
                except ValueError:
                    pass
        time.sleep(0.25)
    return None


def cmd_exec(a: argparse.Namespace) -> int:
    """Push Python into the live editor over the loopback exec server.

    Same 4-byte-length-prefixed framing as `tw.simbridge`: send the source, read
    back whatever it printed. The snippet runs on the editor's game thread."""
    src = a.code
    if Path(src).is_file():
        src = Path(src).read_text()
    port = _read_exec_port()
    if port is None:
        sys.exit("no live editor found — start one with `twctl live`")
    payload = src.encode("utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
            sock.sendall(struct.pack(">I", len(payload)) + payload)
            header = _recv_exact(sock, 4)
            if header is None:
                sys.exit("live editor closed the connection without replying")
            (length,) = struct.unpack(">I", header)
            body = _recv_exact(sock, length) or b""
    except ConnectionRefusedError:
        sys.exit(
            "exec port is stale — the live editor isn't accepting; "
            "is `twctl live` still running?"
        )
    out = body.decode("utf-8")
    if out:
        print(out, end="" if out.endswith("\n") else "\n")
    return 0


def cmd_kill(_a: argparse.Namespace) -> int:
    """Stop the live editor started by `twctl live` (via its recorded pid)."""
    if not EXEC_PORT_FILE.exists():
        sys.exit("no live editor recorded (.exec-port missing)")
    lines = EXEC_PORT_FILE.read_text().splitlines()
    if len(lines) < 2:
        sys.exit(".exec-port has no pid — close the editor window manually")
    pid = int(lines[1])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        EXEC_PORT_FILE.unlink(missing_ok=True)
        sys.exit(f"editor pid {pid} is not running (cleared stale .exec-port)")
    # UE tears down over ~15s and does not run Python shutdown callbacks on a
    # signal exit, so drop the pointer here — we know the editor is on its way out.
    EXEC_PORT_FILE.unlink(missing_ok=True)
    print(f"[twctl] sent SIGTERM to editor pid {pid} (shutting down)")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(prog="twctl", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campaign", default="britain")
    ap.add_argument("--seed", type=int, default=42)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sim").set_defaults(fn=cmd_sim)
    sub.add_parser("build").set_defaults(fn=cmd_build)
    p_shot = sub.add_parser("shot")
    p_shot.add_argument("names", nargs="*", help="preset names (default: all)")
    p_shot.add_argument("--build", action="store_true", help="force a world rebuild first")
    p_shot.set_defaults(fn=cmd_shot)
    sub.add_parser("assets").set_defaults(fn=cmd_assets)
    sub.add_parser("diff").set_defaults(fn=cmd_diff)
    sub.add_parser("bless").set_defaults(fn=cmd_bless)
    sub.add_parser("live").set_defaults(fn=cmd_live)
    p_exec = sub.add_parser("exec")
    p_exec.add_argument("code", help="a .py file path or an inline snippet")
    p_exec.set_defaults(fn=cmd_exec)
    sub.add_parser("kill").set_defaults(fn=cmd_kill)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
