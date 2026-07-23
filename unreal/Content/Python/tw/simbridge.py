"""The client half of the sim bridge — pure Python, no dependencies.

The simulation (``tw_sim.server``, Python 3.14/uv) is a black box we reach over a
loopback TCP socket: a 4-byte big-endian length prefix followed by a msgpack
body, request/response, one in flight (see sim/src/tw_sim/server.py). The old
frontend spoke this from hand-rolled C++; here it is a hundred lines of Python.

Unreal's embedded interpreter ships no ``msgpack`` package, so a minimal codec is
vendored below — just the subset the protocol uses (nil/bool/int/float/str/bin/
array/map). Keeping it dependency-free is what lets this run inside the editor
with nothing installed.

The design rule from the old renderer carries over: **nothing calls the transport
per frame.** A frontend fetches a `WorldSnapshot`, builds actors from it, and only
talks to the sim again on an actual turn/command.
"""

from __future__ import annotations

import socket
import struct
from typing import Any

from . import config

_HEADER = struct.Struct(">I")
MAX_FRAME = 1 << 20


# --------------------------------------------------------------------------- #
# Minimal msgpack codec (encode what we send, decode what we get back).
# --------------------------------------------------------------------------- #
def _pack(obj: Any) -> bytes:
    if obj is None:
        return b"\xc0"
    if obj is True:
        return b"\xc3"
    if obj is False:
        return b"\xc2"
    if isinstance(obj, int):
        return _pack_int(obj)
    if isinstance(obj, float):
        return b"\xcb" + struct.pack(">d", obj)
    if isinstance(obj, str):
        return _pack_str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return _pack_bin(bytes(obj))
    if isinstance(obj, (list, tuple)):
        out = [_pack_len(0x90, 0xDC, len(obj))]
        out += [_pack(v) for v in obj]
        return b"".join(out)
    if isinstance(obj, dict):
        out = [_pack_len(0x80, 0xDE, len(obj))]
        for k, v in obj.items():
            out.append(_pack(k))
            out.append(_pack(v))
        return b"".join(out)
    raise TypeError(f"cannot msgpack {type(obj).__name__}")


def _pack_int(n: int) -> bytes:
    if 0 <= n < 0x80:
        return bytes([n])
    if -0x20 <= n < 0:
        return bytes([n & 0xFF])
    if -0x80 <= n <= 0x7F:
        return b"\xd0" + struct.pack(">b", n)
    if -0x8000 <= n <= 0x7FFF:
        return b"\xd1" + struct.pack(">h", n)
    if -0x8000_0000 <= n <= 0x7FFF_FFFF:
        return b"\xd2" + struct.pack(">i", n)
    return b"\xd3" + struct.pack(">q", n)


def _pack_str(s: str) -> bytes:
    b = s.encode("utf-8")
    n = len(b)
    if n < 0x20:
        return bytes([0xA0 | n]) + b
    if n < 0x100:
        return b"\xd9" + bytes([n]) + b
    if n < 0x1_0000:
        return b"\xda" + struct.pack(">H", n) + b
    return b"\xdb" + struct.pack(">I", n) + b


def _pack_bin(b: bytes) -> bytes:
    n = len(b)
    if n < 0x100:
        return b"\xc4" + bytes([n]) + b
    if n < 0x1_0000:
        return b"\xc5" + struct.pack(">H", n) + b
    return b"\xc6" + struct.pack(">I", n) + b


def _pack_len(fix_base: int, big_prefix: int, n: int) -> bytes:
    """Container header: fix form up to 15, else the 32-bit form (skip the rare
    16-bit form — a snapshot never has 65k of anything, but 32-bit is always
    legal)."""
    if n < 0x10:
        return bytes([fix_base | n])
    return bytes([big_prefix + 1]) + struct.pack(">I", n)


class _Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def _take(self, n: int) -> bytes:
        b = self.buf[self.pos : self.pos + n]
        self.pos += n
        return b

    def read(self) -> Any:
        b = self.buf[self.pos]
        self.pos += 1
        if b < 0x80:
            return b
        if b >= 0xE0:
            return b - 0x100
        if 0x80 <= b <= 0x8F:
            return self._map(b & 0x0F)
        if 0x90 <= b <= 0x9F:
            return self._array(b & 0x0F)
        if 0xA0 <= b <= 0xBF:
            return self._take(b & 0x1F).decode("utf-8")
        if b == 0xC0:
            return None
        if b == 0xC2:
            return False
        if b == 0xC3:
            return True
        if b == 0xC4:
            return self._take(self._u(1))
        if b == 0xC5:
            return self._take(self._u(2))
        if b == 0xC6:
            return self._take(self._u(4))
        if b == 0xCA:
            return struct.unpack(">f", self._take(4))[0]
        if b == 0xCB:
            return struct.unpack(">d", self._take(8))[0]
        if b == 0xCC:
            return self._u(1)
        if b == 0xCD:
            return self._u(2)
        if b == 0xCE:
            return self._u(4)
        if b == 0xCF:
            return self._u(8)
        if b == 0xD0:
            return struct.unpack(">b", self._take(1))[0]
        if b == 0xD1:
            return struct.unpack(">h", self._take(2))[0]
        if b == 0xD2:
            return struct.unpack(">i", self._take(4))[0]
        if b == 0xD3:
            return struct.unpack(">q", self._take(8))[0]
        if b == 0xD9:
            return self._take(self._u(1)).decode("utf-8")
        if b == 0xDA:
            return self._take(self._u(2)).decode("utf-8")
        if b == 0xDB:
            return self._take(self._u(4)).decode("utf-8")
        if b == 0xDC:
            return self._array(self._u(2))
        if b == 0xDD:
            return self._array(self._u(4))
        if b == 0xDE:
            return self._map(self._u(2))
        if b == 0xDF:
            return self._map(self._u(4))
        raise ValueError(f"unsupported msgpack byte 0x{b:02x}")

    def _u(self, n: int) -> int:
        return int.from_bytes(self._take(n), "big")

    def _array(self, n: int) -> list:
        return [self.read() for _ in range(n)]

    def _map(self, n: int) -> dict:
        return {self.read(): self.read() for _ in range(n)}


def _unpack(buf: bytes) -> Any:
    return _Reader(buf).read()


# --------------------------------------------------------------------------- #
# The bridge.
# --------------------------------------------------------------------------- #
class SimError(RuntimeError):
    """A response with ``ok: false`` — a rule refusal or a bad request. Normal
    part of play (an illegal move), not a transport failure."""


class Sim:
    """A single connection to the sidecar. Serial by construction: one request in
    flight, matching the server. Use as a context manager or call `close()`."""

    def __init__(self, port: int | None = None, host: str = "127.0.0.1") -> None:
        port = port or config.sim_port()
        if not port:
            raise ConnectionError(
                "no sim port: start it with `twctl sim` / `make sim`, or pass port="
            )
        self.sock = socket.create_connection((host, port), timeout=10.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def __enter__(self) -> "Sim":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    # -- transport ---------------------------------------------------------- #
    def _rpc(self, req: dict[str, Any]) -> dict[str, Any]:
        body = _pack(req)
        self.sock.sendall(_HEADER.pack(len(body)) + body)
        (n,) = _HEADER.unpack(self._recvn(_HEADER.size))
        if n > MAX_FRAME:
            raise ValueError(f"reply of {n} bytes exceeds the {MAX_FRAME} limit")
        reply = _unpack(self._recvn(n))
        if not reply.get("ok"):
            raise SimError(reply.get("error", "sim refused the request"))
        return reply

    def _recvn(self, n: int) -> bytes:
        chunks = []
        got = 0
        while got < n:
            chunk = self.sock.recv(n - got)
            if not chunk:
                raise ConnectionError("sidecar closed the connection mid-frame")
            chunks.append(chunk)
            got += len(chunk)
        return b"".join(chunks)

    # -- the facade (mirrors api.Simulation) -------------------------------- #
    def init(self, campaign: str = "britain", seed: int = 42) -> dict[str, Any]:
        """Start (or attach to) a campaign; returns the opening snapshot dict."""
        return self._rpc({"op": "init", "campaign": campaign, "seed": seed})["snapshot"]

    def snapshot(self) -> dict[str, Any]:
        return self._rpc({"op": "snapshot"})["snapshot"]

    def command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Apply one player command; returns ``{events, snapshot}``. Raises
        `SimError` on a rule refusal."""
        return self._rpc({"op": "command", "cmd": cmd})

    def end_turn(self) -> dict[str, Any]:
        """Resolve the turn and every AI turn; returns ``{events, snapshot}``."""
        return self._rpc({"op": "end_turn"})


def opening_snapshot(campaign: str = "britain", seed: int = 42) -> dict[str, Any]:
    """Convenience for the read-only build: connect, init, return the snapshot."""
    with Sim() as sim:
        return sim.init(campaign=campaign, seed=seed)
