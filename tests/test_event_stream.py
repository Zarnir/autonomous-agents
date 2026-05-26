"""M21 — A2A read-only event stream tests.

Covers:
  - `make_notification` envelope shape (JSON-RPC 2.0 notification, no `id`)
  - `EventBus.notify` fans out to subscribers without blocking on slow ones
  - `WebSocketServer` accepts a connection, handshakes, and forwards envelopes
    as text frames
  - `orchestrator.log()` and `orchestrator.finalize_story()` emit events when a
    bus is registered, and are no-ops when no bus exists
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _drain(q: queue.Queue, timeout: float = 0.5) -> list[dict]:
    """Drain everything currently in the queue, with a small wait."""
    items: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            items.append(q.get(timeout=0.05))
        except queue.Empty:
            if items:
                break
            continue
    return items


@pytest.fixture
def import_orch(monkeypatch, project_root: Path):
    monkeypatch.chdir(project_root)
    import orchestrator
    yield orchestrator
    orchestrator._EVENT_BUS = None  # don't leak across tests


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------

def test_envelope_shape_is_valid_json_rpc_2_0():
    from event_stream import make_notification

    env = make_notification("event/log_appended", {"msg": "hi"})
    assert env["jsonrpc"] == "2.0"
    assert env["method"] == "event/log_appended"
    assert env["params"] == {"msg": "hi"}
    # JSON-RPC 2.0 notifications have no `id`
    assert "id" not in env


# ---------------------------------------------------------------------------
# EventBus fan-out
# ---------------------------------------------------------------------------

def test_subscribe_and_notify_delivers_envelope():
    from event_stream import EventBus

    bus = EventBus()
    sid, q = bus.subscribe()
    bus.notify("event/log_appended", {"msg": "hello"})

    items = _drain(q)
    assert len(items) == 1
    assert items[0]["method"] == "event/log_appended"
    assert items[0]["params"]["msg"] == "hello"

    bus.unsubscribe(sid)


def test_notify_with_no_subscribers_is_noop():
    from event_stream import EventBus

    bus = EventBus()
    bus.notify("event/x", {})  # should not raise


def test_unsubscribe_stops_delivery():
    from event_stream import EventBus

    bus = EventBus()
    sid, q = bus.subscribe()
    bus.unsubscribe(sid)
    bus.notify("event/x", {})
    assert _drain(q, timeout=0.2) == []


def test_slow_subscriber_dropped_without_blocking_notify():
    """A subscriber whose queue fills up must NOT block notify() — events are
    dropped silently. Correct back-pressure for an observability stream."""
    from event_stream import EventBus

    bus = EventBus(max_queue_per_subscriber=5)
    sid, q = bus.subscribe()

    for i in range(100):
        bus.notify("event/x", {"i": i})  # must not raise or hang

    items = _drain(q)
    assert len(items) <= 5

    bus.unsubscribe(sid)


# ---------------------------------------------------------------------------
# orchestrator.log + finalize_story event emission
# ---------------------------------------------------------------------------

def test_log_fan_out_to_subscriber_when_bus_registered(import_orch):
    from event_stream import EventBus

    orch = import_orch
    bus = EventBus()
    sid, q = bus.subscribe()
    orch._EVENT_BUS = bus

    orch.log("hello world")

    items = _drain(q)
    assert len(items) == 1
    assert items[0]["method"] == "event/log_appended"
    assert "hello world" in items[0]["params"]["msg"]


def test_log_when_bus_unset_is_noop(import_orch):
    """If no bus is registered, log() must work as before."""
    orch = import_orch
    orch._EVENT_BUS = None
    orch.log("plain output")  # must not raise


def test_finalize_story_emits_status_changed_event(import_orch, project_root):
    """finalize_story must emit event/story_status_changed."""
    from event_stream import EventBus

    orch = import_orch
    (project_root / ".opencode").mkdir(exist_ok=True)
    (project_root / ".opencode" / "progress.json").write_text(json.dumps({
        "schema_version": "2.0",
        "version": 1,
        "status": "in_progress",
        "epics": [{"id": "E1", "stories": [{
            "id": "S1",
            "title": "Test story",
            "status": "in_progress",
            "depends_on": [],
            "execution_wave": 1,
            "estimated_complexity": "small",
            "acceptance_criteria": [],
            "tasks": [],
            "artifacts": {"commit_hash": "abc123"},
        }]}],
        "sprints": [],
        "current_sprint": 0,
    }))

    bus = EventBus()
    _sid, q = bus.subscribe()
    orch._EVENT_BUS = bus

    data = orch.read_progress()
    orch.finalize_story(data, "S1", "completed", None)

    items = _drain(q)
    status_events = [it for it in items if it["method"] == "event/story_status_changed"]
    assert len(status_events) == 1
    params = status_events[0]["params"]
    assert params["story_id"] == "S1"
    assert params["to"] == "completed"


# ---------------------------------------------------------------------------
# WebSocketServer end-to-end (real socket, minimal handshake)
# ---------------------------------------------------------------------------

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_handshake_client(host: str, port: int) -> socket.socket:
    """Connect + minimal RFC 6455 client handshake. Returns the socket."""
    sock = socket.create_connection((host, port), timeout=2.0)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("latin-1"))
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("server closed during handshake")
        data += chunk
    assert b"101 Switching Protocols" in data, f"bad handshake: {data!r}"
    expected = base64.b64encode(
        hashlib.sha1((key + _WS_MAGIC).encode()).digest()
    ).decode()
    assert expected.encode() in data
    return sock


def _read_text_frame(sock: socket.socket, timeout: float = 2.0) -> str:
    """Read one server-to-client text frame (no masking per RFC 6455 §5.1)."""
    sock.settimeout(timeout)
    header = sock.recv(2)
    if len(header) < 2:
        raise RuntimeError("connection closed")
    b0, b1 = header[0], header[1]
    assert b0 & 0x80, "expected FIN"
    assert (b0 & 0x0F) == 0x1, "expected text frame"
    assert not (b1 & 0x80), "server frames must not be masked"
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]
    payload = b""
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise RuntimeError("short read")
        payload += chunk
    return payload.decode("utf-8")


def test_websocket_server_forwards_envelopes_to_client():
    """E2E: bus → server → real socket → JSON-RPC envelope decoded by client."""
    from event_stream import EventBus, WebSocketServer

    bus = EventBus()
    server = WebSocketServer(bus, host="127.0.0.1", port=0)  # ephemeral
    server.start()
    try:
        client = _ws_handshake_client("127.0.0.1", server.port)
        time.sleep(0.1)  # let server register the subscriber
        bus.notify("event/log_appended", {"msg": "stream test"})
        frame_text = _read_text_frame(client)
        envelope = json.loads(frame_text)
        assert envelope["jsonrpc"] == "2.0"
        assert envelope["method"] == "event/log_appended"
        assert envelope["params"]["msg"] == "stream test"
        client.close()
    finally:
        server.stop()


def test_websocket_server_starts_and_stops_cleanly():
    """Server can be started and stopped multiple times without leaking."""
    from event_stream import EventBus, WebSocketServer

    bus = EventBus()
    for _ in range(2):
        server = WebSocketServer(bus, host="127.0.0.1", port=0)
        server.start()
        assert server.port > 0
        s = socket.create_connection(("127.0.0.1", server.port), timeout=1.0)
        s.close()
        server.stop()


# ---------------------------------------------------------------------------
# cmd_serve integration
# ---------------------------------------------------------------------------

import argparse


def test_cmd_serve_starts_bus_dispatches_to_status_and_cleans_up(
    import_orch, monkeypatch
):
    """The serve subcommand must: bind a real server, set _EVENT_BUS for the
    duration of the inner command, dispatch to the chosen inner cmd, and
    unset _EVENT_BUS on the way out."""
    orch = import_orch

    seen = {"bus_during_inner": None}

    def fake_status(_a):
        seen["bus_during_inner"] = orch._EVENT_BUS
        return 0

    monkeypatch.setattr(orch, "cmd_status", fake_status)

    args = argparse.Namespace(
        host="127.0.0.1", port=0, cmd_to_run="status",
    )
    rc = orch.cmd_serve(args)
    assert rc == 0
    assert seen["bus_during_inner"] is not None, "inner cmd should see _EVENT_BUS set"
    assert orch._EVENT_BUS is None, "bus must be cleared on exit"


def test_cmd_serve_dispatches_to_develop(import_orch, monkeypatch):
    orch = import_orch
    called = []
    monkeypatch.setattr(orch, "cmd_develop",
                        lambda a: called.append(("develop", a)) or 0)

    args = argparse.Namespace(
        host="127.0.0.1", port=0, cmd_to_run="develop",
    )
    rc = orch.cmd_serve(args)
    assert rc == 0
    assert len(called) == 1 and called[0][0] == "develop"


def test_cmd_serve_dispatches_to_resume(import_orch, monkeypatch):
    orch = import_orch
    called = []
    monkeypatch.setattr(orch, "cmd_resume",
                        lambda a: called.append(("resume", a)) or 0)

    args = argparse.Namespace(
        host="127.0.0.1", port=0, cmd_to_run="resume",
    )
    rc = orch.cmd_serve(args)
    assert rc == 0
    assert len(called) == 1 and called[0][0] == "resume"


def test_cmd_serve_rejects_unknown_inner_command(import_orch, monkeypatch):
    """Passing an unknown --cmd value should die with a clear message."""
    orch = import_orch
    args = argparse.Namespace(
        host="127.0.0.1", port=0, cmd_to_run="bogus",
    )
    with pytest.raises(SystemExit):
        orch.cmd_serve(args)
    # Cleanup invariant: bus is cleared even on the die path
    assert orch._EVENT_BUS is None


# ---------------------------------------------------------------------------
# Coverage closers — frame encoding edge cases, handshake reject paths
# ---------------------------------------------------------------------------

def test_encode_text_frame_medium_length_uses_2_byte_extended():
    """Lengths 126..65535 use the 2-byte extended-length encoding."""
    from event_stream import _encode_text_frame

    payload = "x" * 200  # in [126, 65535]
    frame = _encode_text_frame(payload)
    # Bytes: [FIN+text=0x81] [126] [length_hi] [length_lo] [payload...]
    assert frame[1] == 126
    assert struct.unpack(">H", frame[2:4])[0] == 200


def test_encode_text_frame_large_length_uses_8_byte_extended():
    """Lengths >= 65536 use the 8-byte extended-length encoding."""
    from event_stream import _encode_text_frame

    payload = "x" * 70_000  # > 65535
    frame = _encode_text_frame(payload)
    assert frame[1] == 127
    assert struct.unpack(">Q", frame[2:10])[0] == 70_000


def test_handshake_rejects_request_without_sec_websocket_key():
    """A request that arrives without `Sec-WebSocket-Key` must be rejected."""
    from event_stream import _do_handshake

    server_sock, client_sock = socket.socketpair()
    # Send a complete HTTP request that lacks the Sec-WebSocket-Key header
    client_sock.sendall(
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n\r\n"
    )
    assert _do_handshake(server_sock) is False
    server_sock.close()
    client_sock.close()


def test_handshake_rejects_connection_closed_mid_request():
    """If the client closes the socket before sending headers, handshake fails."""
    from event_stream import _do_handshake

    server_sock, client_sock = socket.socketpair()
    client_sock.close()  # client disappears immediately
    assert _do_handshake(server_sock) is False
    server_sock.close()


def test_serve_connection_returns_when_handshake_fails():
    """Cover the `return` at event_stream.py line 215: handshake returns False
    (e.g., client sent a valid HTTP request but no Sec-WebSocket-Key), so
    `_serve_connection` cleanly disposes of the connection without subscribing."""
    from event_stream import EventBus, WebSocketServer

    bus = EventBus()
    server = WebSocketServer(bus, host="127.0.0.1", port=0)
    server.start()
    try:
        s = socket.create_connection(("127.0.0.1", server.port), timeout=1.0)
        # Valid HTTP, but no Sec-WebSocket-Key → handshake returns False
        s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        time.sleep(0.2)  # give the server thread time to run _do_handshake
        s.close()
    finally:
        server.stop()


def test_handshake_rejects_oversized_request():
    """A request that exceeds 8 KiB before terminator must be rejected (DOS cap).

    socketpair() default buffers are smaller than 9 KiB, so the client's sendall
    would block before the server starts reading. We run `_do_handshake` in a
    thread to break the deadlock.
    """
    from event_stream import _do_handshake

    server_sock, client_sock = socket.socketpair()
    result: list[bool] = []

    def run_server():
        result.append(_do_handshake(server_sock))

    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    try:
        client_sock.sendall(b"GET / HTTP/1.1\r\nX-Big: " + b"A" * 9000)
    except (BrokenPipeError, ConnectionResetError):
        # Server may close mid-send after hitting the cap
        pass
    t.join(timeout=4.0)
    assert result == [False], f"expected handshake to reject, got {result}"
    try:
        server_sock.close()
    except OSError:
        pass
    try:
        client_sock.close()
    except OSError:
        pass
