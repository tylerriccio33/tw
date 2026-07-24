"""In-editor TCP eval server for the ``twctl exec`` tight loop.

Started only when ``twctl live`` sets ``TW_EXEC_SERVER=1`` (see
``init_unreal.py``). It replaces Epic's UDP-multicast Python Remote Execution —
which macOS's Local Network privacy gate silently blackholes, so ``twctl exec``
could never discover the editor — with a plain loopback TCP channel plus a port
file, exactly the pattern ``twctl sim`` already uses (``sim/.sim-port``). Nothing
here needs multicast, discovery, or an OS permission prompt: loopback TCP is not
gated.

Wire protocol, both directions: a 4-byte big-endian length prefix followed by
that many UTF-8 bytes (the same framing as ``tw.simbridge``). The request payload
is Python source; the response payload is whatever that source wrote to
stdout/stderr, or a traceback if it raised.

UE's Python may only touch the engine from the game thread, so the socket accept
loop runs on a daemon thread but hands each snippet to a Slate post-tick callback
(which fires on the game thread) and blocks until it has run.
"""

from __future__ import annotations

import io
import os
import socket
import struct
import sys
import threading
import traceback
from pathlib import Path

import unreal

# exec_server.py lives at unreal/Content/Python/; parents[2] is unreal/.
PORT_FILE = Path(__file__).resolve().parents[2] / ".exec-port"

# Persistent namespace so state survives across exec calls, like a REPL session.
_NS: dict = {"__name__": "__twctl_exec__", "unreal": unreal}

# Game-thread work queue: each item is (source, done_event, result_holder).
_QUEUE: list[tuple[str, threading.Event, list[str]]] = []
_QLOCK = threading.Lock()
_STARTED = False


def _run_source(source: str) -> str:
    """Exec ``source`` in the persistent namespace, capturing what it prints."""
    buf = io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        exec(compile(source, "<twctl-exec>", "exec"), _NS)  # noqa: S102
    except Exception:  # noqa: BLE001 - report to the caller, never crash the editor
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = saved
    return buf.getvalue()


def _drain(_delta: float) -> None:
    """Slate post-tick callback: run queued snippets on the game thread."""
    with _QLOCK:
        jobs = _QUEUE[:]
        _QUEUE.clear()
    for source, done, holder in jobs:
        try:
            holder.append(_run_source(source))
        except Exception:  # noqa: BLE001
            holder.append(traceback.format_exc())
        finally:
            done.set()


def _dispatch(source: str) -> str:
    """Queue ``source`` for the game thread and block until it has run."""
    done = threading.Event()
    holder: list[str] = []
    with _QLOCK:
        _QUEUE.append((source, done, holder))
    done.wait()
    return holder[0] if holder else ""


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    chunks: list[bytes] = []
    while n > 0:
        chunk = conn.recv(n)
        if not chunk:
            return None
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def _serve(server: socket.socket) -> None:
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            header = _recv_exact(conn, 4)
            if header is None:
                continue
            (length,) = struct.unpack(">I", header)
            payload = _recv_exact(conn, length)
            if payload is None:
                continue
            output = _dispatch(payload.decode("utf-8"))
            data = output.encode("utf-8")
            conn.sendall(struct.pack(">I", len(data)) + data)


def _cleanup() -> None:
    PORT_FILE.unlink(missing_ok=True)


def start() -> None:
    """Bind loopback, publish the port + pid, and serve forever (idempotent)."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    port = server.getsockname()[1]

    # Line 1 is the port (`twctl exec`); line 2 is the pid (`twctl kill`).
    PORT_FILE.write_text(f"{port}\n{os.getpid()}\n")
    unreal.register_slate_post_tick_callback(_drain)
    unreal.register_python_shutdown_callback(_cleanup)
    threading.Thread(target=_serve, args=(server,), daemon=True).start()
    unreal.log(f"[twctl] exec server on 127.0.0.1:{port} (pid {os.getpid()})")
