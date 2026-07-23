"""The engine-free bridge test — the fast pre-commit gate for the sim bridge.

Plays the role the old `make cpp-test` did: exercise the whole wire protocol
against a *real* `tw_sim` sidecar, in seconds, with no editor. Here the bridge is
pure Python (`tw.simbridge`), so this also cross-checks the vendored msgpack codec
against the reference `msgpack` library (available in the sim venv).

Run via `make bridge-test`. Exits non-zero on any failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PYDIR = HERE.parents[1]  # unreal/Content/Python
REPO = HERE.parents[4]
SIM = REPO / "sim"

sys.path.insert(0, str(PYDIR))


def _check(cond: bool, msg: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {msg}")
    if not cond:
        raise SystemExit(1)


def _codec() -> None:
    import msgpack  # from the sim venv

    from tw.simbridge import _pack, _unpack

    samples = [
        None, True, False, 0, 1, -1, 127, -32, 255, -128, 65535, -32768,
        2**31 - 1, -(2**31), 2**40, -(2**40), 3.14, "", "hi", "x" * 40, "u" * 300,
        [1, 2, [3, "four"]], {"a": 1, "b": [True, None], "nested": {"k": "v"}},
        {"kind": "move", "army": 42, "to": 7},
    ]
    for s in samples:
        ref = msgpack.unpackb(msgpack.packb(s, use_bin_type=True), raw=False, strict_map_key=False)
        _check(_unpack(_pack(s)) == ref, f"codec round-trips {type(s).__name__}")
        _check(_unpack(msgpack.packb(s, use_bin_type=True)) == ref, "decodes server-packed")


def _sidecar() -> None:
    port_file = SIM / ".sim-port-test"
    port_file.unlink(missing_ok=True)
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "tw_sim.server", "--port-file", ".sim-port-test"],
        cwd=SIM, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            time.sleep(0.1)
        os.environ["TW_SIM_PORT"] = port_file.read_text().strip()

        from tw.simbridge import Sim, SimError

        with Sim() as sim:
            snap = sim.init(campaign="britain", seed=42)
            _check(snap["turn"] == 1, "init succeeds, turn == 1")
            _check(len(snap["provinces"]) == 12, "britain has 12 provinces")
            _check(len(snap["factions"]) == 5, "britain has 5 factions")
            r = sim.end_turn()
            _check(r["snapshot"]["turn"] == 2, "end_turn advances the turn")
            try:
                sim.command({"kind": "move", "army": 999, "to": 0})
                _check(False, "an illegal move is refused")
            except SimError:
                _check(True, "an illegal move is refused")
    finally:
        proc.terminate()
        port_file.unlink(missing_ok=True)


def main() -> int:
    print("codec")
    _codec()
    print("sidecar")
    _sidecar()
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
