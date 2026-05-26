"""M21 — A2A read-only event stream over WebSocket.

In-process pub/sub (`EventBus`) plus a stdlib-only WebSocket server
(`WebSocketServer`) that broadcasts JSON-RPC 2.0 notifications to every
connected subscriber. Read-only by design: clients subscribe; they cannot
mutate orchestrator state. Bound to 127.0.0.1 by default — auth is
deferred until Integration Point D.

Why stdlib-only: the project ships with zero runtime dependencies. Adding
`websockets` for ~130 lines of frame plumbing was a bad trade. The
server-only direction simplifies framing — server frames are never masked
per RFC 6455 §5.1.

Envelope shape (JSON-RPC 2.0 notification):

    {"jsonrpc": "2.0", "method": "event/<name>", "params": {...}}
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import socket
import struct
import threading
from typing import Optional


_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------------------------------------------------------------------
# JSON-RPC envelope
# ---------------------------------------------------------------------------

def make_notification(method: str, params: dict) -> dict:
    """Build a JSON-RPC 2.0 notification (no `id` field)."""
    return {"jsonrpc": "2.0", "method": method, "params": params}


# ---------------------------------------------------------------------------
# EventBus — thread-safe in-process pub/sub
# ---------------------------------------------------------------------------

class EventBus:
    """In-process publisher/subscriber with bounded per-subscriber queues.

    `notify()` never blocks: if a subscriber's queue is full, the event is
    dropped silently. Correct back-pressure for an observability stream —
    a slow dashboard must never freeze the orchestrator.
    """

    def __init__(self, max_queue_per_subscriber: int = 1000):
        self._lock = threading.Lock()
        self._subscribers: dict[int, queue.Queue] = {}
        self._next_id = 0
        self._max_q = max_queue_per_subscriber

    def subscribe(self) -> tuple[int, queue.Queue]:
        """Register a new subscriber. Returns (subscriber_id, queue)."""
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            q: queue.Queue = queue.Queue(maxsize=self._max_q)
            self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subscribers.pop(sid, None)

    def notify(self, method: str, params: dict) -> None:
        """Broadcast a notification. Never blocks; drops on full queues."""
        envelope = make_notification(method, params)
        with self._lock:
            subs = list(self._subscribers.values())
        for q in subs:
            try:
                q.put_nowait(envelope)
            except queue.Full:
                pass  # slow subscriber → drop


# ---------------------------------------------------------------------------
# WebSocket frame helpers (stdlib only)
# ---------------------------------------------------------------------------

_FIN = 0x80
_OPCODE_TEXT = 0x1


def _encode_text_frame(payload: str) -> bytes:
    """Encode a single FIN'd text frame. Server frames are never masked."""
    data = payload.encode("utf-8")
    length = len(data)
    header = bytes([_FIN | _OPCODE_TEXT])
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack(">H", length)
    else:
        header += bytes([127]) + struct.pack(">Q", length)
    return header + data


def _do_handshake(sock: socket.socket) -> bool:
    """Read the HTTP upgrade, send the 101 response. Returns True on success."""
    data = b""
    sock.settimeout(2.0)
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return False
        data += chunk
        if len(data) > 8192:  # cap to avoid DOS
            return False
    headers: dict[str, str] = {}
    for line in data.decode("latin-1", errors="replace").split("\r\n")[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    key = headers.get("sec-websocket-key")
    if not key:
        return False
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode()).digest()
    ).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    sock.sendall(response.encode("latin-1"))
    return True


# ---------------------------------------------------------------------------
# WebSocketServer
# ---------------------------------------------------------------------------

class WebSocketServer:
    """Stdlib WebSocket server that broadcasts EventBus envelopes.

    Read-only — incoming frames from clients are ignored. One accept thread
    plus one thread per connection. Use `port=0` to bind an ephemeral port;
    the actual port is then available via `server.port` after `start()`.
    """

    def __init__(self, bus: EventBus, host: str = "127.0.0.1", port: int = 8765):
        self._bus = bus
        self._host = host
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._connections: list[socket.socket] = []
        self._actual_port: Optional[int] = None

    @property
    def port(self) -> int:
        return self._actual_port if self._actual_port is not None else self._port

    def start(self) -> None:
        self._stop.clear()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(8)
        self._actual_port = self._sock.getsockname()[1]
        self._sock.settimeout(0.5)
        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._accept_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:  # pragma: no cover — defensive; close on already-closed
                pass
            self._sock = None
        for c in list(self._connections):
            try:
                c.close()
            except OSError:  # pragma: no cover — defensive; close on already-closed
                pass
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
            self._accept_thread = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                assert self._sock is not None
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:  # pragma: no cover — fires when stop() closes the listening socket
                break
            self._connections.append(conn)
            threading.Thread(
                target=self._serve_connection, args=(conn,), daemon=True
            ).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        sid: Optional[int] = None
        try:
            try:
                if not _do_handshake(conn):
                    return
            except OSError:  # pragma: no cover — race: stop() closes socket mid-handshake
                # Client (or our own stop()) closed the socket between accept
                # and handshake; nothing to clean up beyond the finally block.
                return
            sid, q = self._bus.subscribe()
            try:
                conn.settimeout(None)
            except OSError:  # pragma: no cover — defensive; conn closed mid-setup
                return
            while not self._stop.is_set():
                try:
                    envelope = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    conn.sendall(_encode_text_frame(json.dumps(envelope)))
                except OSError:  # pragma: no cover — defensive; client disconnected mid-send
                    break
        finally:
            if sid is not None:
                self._bus.unsubscribe(sid)
            try:
                conn.close()
            except OSError:  # pragma: no cover — defensive; close on already-closed
                pass
            try:
                self._connections.remove(conn)
            except ValueError:  # pragma: no cover — defensive; conn already removed by stop()
                pass
