"""Drive the socket server end to end with no frontend involved.

This is the engine-independence claim, tested: init -> command -> end_turn ->
snapshot over a real socket, with nothing but msgpack on the other end.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

import msgpack
import pytest

from tw_sim import command as cmds, event as ev
from tw_sim.command import from_dict as command_from_dict, to_dict as command_to_dict
from tw_sim.server import Session, _serve_client, serve

_HEADER = struct.Struct(">I")


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def call(self, **req: Any) -> dict[str, Any]:
        body = msgpack.packb(req, use_bin_type=True)
        self.writer.write(_HEADER.pack(len(body)) + body)
        await self.writer.drain()
        (n,) = _HEADER.unpack(await self.reader.readexactly(_HEADER.size))
        return msgpack.unpackb(
            await self.reader.readexactly(n), raw=False, strict_map_key=False
        )


def test_socket_roundtrip_init_command_end_turn_snapshot():
    async def scenario():
        # `serve` differs from this only in printing the port it picked, which
        # a test has no use for; bind directly so we know the port up front.
        session = Session()
        server = await asyncio.start_server(
            lambda r, w: _serve_client(session, r, w), "127.0.0.1", 0
        )
        port = server.sockets[0].getsockname()[1]
        client = Client(*await asyncio.open_connection("127.0.0.1", port))

        r = await client.call(op="init", campaign="britain", seed=42)
        assert r["ok"]
        snap = r["snapshot"]
        assert snap["turn"] == 1
        assert len(snap["provinces"]) == 12
        assert len(snap["factions"]) == 5
        # England's founding army, 8 melee in London.
        army = next(a for a in snap["armies"] if a["owner"] == 0)
        assert army["regiments"] == 8 and army["location"] == 0

        # A legal command: London (0) -> York (1).
        r = await client.call(
            op="command",
            cmd=command_to_dict(cmds.Move(army=army["id"], to=1)),
        )
        assert r["ok"], r
        assert any(e["kind"] == "moved" and e["to"] == 1 for e in r["events"])

        # An illegal one comes back as a refusal, not an exception.
        r = await client.call(
            op="command",
            cmd=command_to_dict(cmds.Move(army=army["id"], to=11)),
        )
        assert not r["ok"]
        assert r["rule"] == "NOT_ADJACENT"

        # One end_turn resolves the player plus every AI faction.
        r = await client.call(op="end_turn")
        assert r["ok"]
        assert r["snapshot"]["current"] == 0, "control returns to the player"
        assert any(e["kind"] == "turn_began" for e in r["events"])

        r = await client.call(op="snapshot")
        assert r["ok"] and r["snapshot"]["turn"] >= 1

        r = await client.call(op="nonsense")
        assert not r["ok"] and "unknown op" in r["error"]

        client.writer.close()
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


def test_every_command_and_event_survives_the_wire():
    """The tagged-union encoding has to cover all 11 commands and all 22 events
    — that is the whole contract the C++ side will be written against."""
    from tw_sim.command import COMMANDS
    from tw_sim.event import EVENTS
    from tw_sim.state import Building, ProposalKind, UnitType

    assert len(COMMANDS) == 11
    assert len(EVENTS) == 22

    samples = [
        cmds.Move(army=1, to=2),
        cmds.Assault(army=1),
        cmds.Besiege(army=1),
        cmds.Recruit(province=0, unit=UnitType.CAV),
        cmds.Build(province=0, building=Building.WALLS),
        cmds.RaiseArmy(province=0, regiments=3),
        cmds.Merge(from_=1, into=2),
        cmds.Garrison(army=1),
        cmds.DeclareWar(on=3),
        cmds.Propose(to=3, treaty=ProposalKind.ALLIANCE),
        cmds.Respond(proposal=0, accept=True),
    ]
    assert {type(s) for s in samples} == set(COMMANDS.values())
    for cmd in samples:
        raw = command_to_dict(cmd)
        assert raw["kind"] == cmd.kind
        assert command_from_dict(msgpack.unpackb(msgpack.packb(raw))) == cmd

    # `from` is a Python keyword, so those fields are `from_` in Python and
    # plain `from` on the wire. That translation is easy to get wrong once.
    raw = ev.to_dict(ev.CityFell(province=3, from_=1, to=0))
    assert raw == {"kind": "city_fell", "province": 3, "from": 1, "to": 0}
    assert ev.from_dict(raw) == ev.CityFell(province=3, from_=1, to=0)


def test_unknown_wire_kinds_are_rejected_loudly():
    with pytest.raises(ValueError, match="unknown wire kind"):
        command_from_dict({"kind": "teleport", "army": 1})
    with pytest.raises(ValueError, match="missing field 'to'"):
        command_from_dict({"kind": "move", "army": 1})


def test_the_port_file_advertises_the_sidecar_and_is_cleaned_up(tmp_path):
    """A frontend that did not spawn us finds us through this file, so a stale
    one pointing at a dead port is the failure mode worth guarding."""
    port_file = tmp_path / ".sim-port"

    async def scenario():
        task = asyncio.ensure_future(serve("127.0.0.1", 0, port_file))
        # Give the server a moment to bind and write the file.
        for _ in range(100):
            if port_file.exists():
                break
            await asyncio.sleep(0.01)

        assert port_file.exists()
        port = int(port_file.read_text().strip())

        client = Client(*await asyncio.open_connection("127.0.0.1", port))
        assert (await client.call(op="init", campaign="britain", seed=42))["ok"]
        client.writer.close()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not port_file.exists(), "a stale port file would misdirect the next session"
