"""Private worker control channel (LAB-026, ADR-0004).

A filesystem Unix stream socket inside the owner-private runtime directory.
Frames are a 4-byte big-endian length plus a UTF-8 JSON object. Requests are
bounded to 64 KiB, responses to 1 MiB. The server checks SO_PEERCRED against
its own uid and every request against its generation id. There is no TCP
listener and no generic command channel.
"""

from __future__ import annotations

import json
import os
import socket
import struct

from .errors import LabError

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 1024 * 1024
PROTOCOL = 1


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks = []
    while n:
        chunk = sock.recv(min(n, 65536))
        if not chunk:
            raise ConnectionError("peer closed the channel")
        chunks.append(chunk)
        n -= len(chunk)
    return b"".join(chunks)


def send_frame(sock: socket.socket, obj: dict, limit: int) -> None:
    data = json.dumps(obj, sort_keys=True, default=str).encode()
    if len(data) > limit:
        raise ValueError("frame exceeds limit")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_frame(sock: socket.socket, limit: int) -> dict:
    (length,) = struct.unpack(">I", _recv_exact(sock, 4))
    if length > limit:
        raise ValueError("frame exceeds limit")
    obj = json.loads(_recv_exact(sock, length))
    if not isinstance(obj, dict):
        raise ValueError("frame is not an object")
    return obj


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def call(socket_path: str, generation_id: str, op: str, params: dict | None = None, timeout: float = 60.0) -> dict:
    """Send one request; return the ``result`` or raise the mapped LabError."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.connect(socket_path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise LabError("transport_error", "sandbox worker channel is not available", {"op": op}) from exc
        if peer_uid(sock) != os.getuid():
            raise LabError("unsafe_configuration", "worker channel is owned by another user")
        send_frame(sock, {"v": PROTOCOL, "generation": generation_id, "op": op, "params": params or {}}, MAX_REQUEST)
        response = recv_frame(sock, MAX_RESPONSE)
    except TimeoutError as exc:
        raise LabError("timeout", "sandbox worker did not answer in time", {"op": op}) from exc
    except (ConnectionError, ValueError, OSError) as exc:
        raise LabError("transport_error", "sandbox worker channel failed", {"op": op, "reason": type(exc).__name__}) from exc
    finally:
        sock.close()
    if response.get("ok"):
        return response.get("result", {})
    err = response.get("error", {})
    code = err.get("code", "internal_error")
    try:
        raise LabError(code, err.get("message", "worker error"), err.get("details"))
    except ValueError:
        raise LabError("internal_error", "worker returned an unknown error code") from None
