#!/usr/bin/env python3
"""Driver for the tw_sim sidecar's msgpack/TCP protocol.

Usage (run from `sim/`, with `uv run`):

    uv run python .claude/skills/run-tw-sim/driver.py smoke

`smoke` spawns `python -m tw_sim.server`, connects, sends `init`, inspects the
snapshot, issues one real command (moves the player's first army to an
adjacent province if one is free), ends the turn, and prints a summary. It is
the fastest way to prove the whole init -> command -> end_turn loop actually
works end to end, without touching Unreal.

Can also be imported and driven interactively:

    from driver import SimClient
    c = SimClient(port)
    c.request({"op": "snapshot"})
"""

from __future__ import annotations

import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import msgpack

_HEADER = struct.Struct(">I")


class SimClient:
    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.sock = socket.create_connection((host, port), timeout=5)

    def request(self, payload: dict) -> dict:
        body = msgpack.packb(payload, use_bin_type=True)
        self.sock.sendall(_HEADER.pack(len(body)) + body)
        (n,) = _HEADER.unpack(self._recv_exact(_HEADER.size))
        return msgpack.unpackb(self._recv_exact(n), raw=False, strict_map_key=False)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("sidecar closed the connection")
            buf += chunk
        return buf

    def close(self) -> None:
        self.sock.close()


def spawn_server(port_file: Path) -> tuple[subprocess.Popen, int]:
    if port_file.exists():
        port_file.unlink()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tw_sim.server", "--port-file", str(port_file)],
        cwd=Path(__file__).resolve().parents[3] / "sim",
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().strip()
    port = int(line)
    return proc, port


def smoke() -> None:
    port_file = Path(__file__).resolve().parents[3] / "sim" / ".sim-port.driver"
    proc, port = spawn_server(port_file)
    print(f"sidecar up on port {port} (pid {proc.pid})")
    try:
        c = SimClient(port)

        r = c.request({"op": "init", "campaign": "britain", "seed": 42})
        assert r["ok"], r
        snap = r["snapshot"]
        print(f"init ok: {len(snap['provinces'])} provinces, {len(snap['factions'])} factions")

        player = next(f for f in snap["factions"] if f["is_player"])
        my_armies = [a for a in snap["armies"] if a["owner"] == player["id"]]
        print(f"player faction: {player['name']}, {len(my_armies)} armies")

        if my_armies:
            army = my_armies[0]
            prov = next(p for p in snap["provinces"] if p["id"] == army["location"])
            target = prov["adjacent"][0] if prov["adjacent"] else None
            if target is not None:
                r = c.request({"op": "command", "cmd": {"kind": "move", "army": army["id"], "to": target}})
                print(f"move army {army['id']} {prov['id']}->{target}: ok={r['ok']}"
                      + ("" if r["ok"] else f" ({r.get('error')})"))

        r = c.request({"op": "end_turn"})
        assert r["ok"], r
        print(f"end_turn ok: {len(r['events'])} events, now turn {r['snapshot']['turn']}")

        r = c.request({"op": "snapshot"})
        assert r["ok"], r
        print(f"final snapshot fetched ok, turn={r['snapshot']['turn']}")

        c.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        port_file.unlink(missing_ok=True)
        print("sidecar stopped")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if cmd == "smoke":
        smoke()
    else:
        print(f"unknown command {cmd!r}; only 'smoke' is implemented", file=sys.stderr)
        sys.exit(1)
