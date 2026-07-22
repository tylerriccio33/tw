"""The socket sidecar: a ~100-line adapter from the wire protocol onto `api.Simulation`.

Length-prefixed msgpack over a loopback TCP socket. Request/response, one in
flight at a time — the interface is genuinely serial (a click, a turn), so there
is nothing to multiplex.

    {"op": "init",    "campaign": "britain", "seed": 42} -> {"ok": true, "snapshot": {...}}
    {"op": "command", "cmd": {"kind": "move", ...}}      -> {"ok": true, "events": [...]}
                                                        -> {"ok": false, "error": "not adjacent"}
    {"op": "end_turn"}                                   -> {"ok": true, "events": [...]}
    {"op": "snapshot"}                                   -> {"ok": true, "snapshot": {...}}

Zero game logic lives here, which is the point: a second adapter for a CLI or a
gym environment is another hundred lines against the same facade. `RuleError` —
the one failure that is a normal part of play — comes back as `ok: false`;
anything else propagates so bugs are loud rather than swallowed into a frontend.
"""

from __future__ import annotations

import argparse
import asyncio
import struct
from pathlib import Path
from typing import Any

import msgpack

from .api import Simulation, events_to_wire
from .command import from_dict as command_from_dict
from .rules import RuleError

#: 4-byte big-endian length prefix, then that many bytes of msgpack.
_HEADER = struct.Struct(">I")
#: Refuse absurd frames rather than allocating on a bad prefix.
MAX_FRAME = 1 << 20


class Session:
    """One connection's view of the simulation. `init` is what creates it, so a
    client that reconnects to a live sidecar picks up the campaign in progress.
    """

    def __init__(self) -> None:
        self.sim: Simulation | None = None

    def dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        op = req.get("op")
        if op == "init":
            self.sim = Simulation(
                campaign=req.get("campaign", "britain"), seed=int(req.get("seed", 42))
            )
            return {"ok": True, "snapshot": self.sim.snapshot().to_dict()}

        sim = self.sim
        if sim is None:
            return {"ok": False, "error": "no campaign: send op=init first"}

        match op:
            case "snapshot":
                return {"ok": True, "snapshot": sim.snapshot().to_dict()}
            case "command":
                try:
                    events = sim.apply(command_from_dict(req["cmd"]))
                except RuleError as e:
                    return {"ok": False, "error": str(e), "rule": e.rule.name}
                return {
                    "ok": True,
                    "events": events_to_wire(events),
                    "snapshot": sim.snapshot().to_dict(),
                }
            case "end_turn":
                events = sim.end_turn()
                return {
                    "ok": True,
                    "events": events_to_wire(events),
                    "snapshot": sim.snapshot().to_dict(),
                }
            case _:
                return {"ok": False, "error": f"unknown op {op!r}"}


async def _read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    header = await reader.readexactly(_HEADER.size)
    (n,) = _HEADER.unpack(header)
    if n > MAX_FRAME:
        raise ValueError(f"frame of {n} bytes exceeds the {MAX_FRAME}-byte limit")
    return msgpack.unpackb(await reader.readexactly(n), raw=False, strict_map_key=False)


async def _write_frame(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
    body = msgpack.packb(payload, use_bin_type=True)
    writer.write(_HEADER.pack(len(body)) + body)
    await writer.drain()


async def _serve_client(
    session: Session, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            try:
                req = await _read_frame(reader)
            except asyncio.IncompleteReadError:
                return  # client hung up between frames; that is a clean exit
            await _write_frame(writer, session.dispatch(req))
    finally:
        writer.close()


async def serve(
    host: str = "127.0.0.1", port: int = 0, port_file: Path | None = None
) -> None:
    """Run until cancelled, printing the chosen port on stdout so a frontend
    that spawned us can read it (port 0 means 'let the OS pick').

    `port_file` additionally leaves the port on disk, which is how a frontend
    *nobody* spawned finds us: run the sidecar by hand, leave the editor open,
    and restarting Python picks the campaign back up without restarting Unreal.
    The file is removed on the way out so a stale one cannot send the next
    session to a dead port.
    """
    session = Session()
    server = await asyncio.start_server(
        lambda r, w: _serve_client(session, r, w), host, port
    )
    chosen = server.sockets[0].getsockname()[1]
    print(chosen, flush=True)
    if port_file is not None:
        port_file.write_text(f"{chosen}\n")
    try:
        async with server:
            await server.serve_forever()
    finally:
        if port_file is not None:
            port_file.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="tw_sim.server", description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument(
        "--port-file",
        type=Path,
        help="also write the chosen port here, so a frontend can attach to an "
        "already-running sidecar instead of spawning its own",
    )
    args = ap.parse_args()
    try:
        asyncio.run(serve(args.host, args.port, args.port_file))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
