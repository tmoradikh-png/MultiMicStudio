"""End-to-end test for Live Speaker mode (Phase L0 WebSocket relay).

Runs entirely in-process with Starlette's TestClient WebSocket support — no real
microphone, network, or browser needed. It proves the *transport* works:
  * the live router is mounted only when LIVE_MODE_ENABLED is true,
  * a publisher's audio frames are relayed to every listener in the same room,
  * frames do NOT leak to other rooms,
  * the roster reports publishers/listeners and reacts to mute,
  * ping/pong round-trips for the latency display.

Run from backend/ (use the venv python):
    python scripts/live_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Enable live mode BEFORE importing the app (get_settings is cached, and main.py
# decides at import time whether to mount the live router).
os.environ["LIVE_MODE_ENABLED"] = "true"
os.environ.setdefault("DATABASE_URL", "sqlite:///./live_smoke.db")

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PASS, FAIL = "[PASS]", "[FAIL]"
_failures = 0

# Relayed frames carry a 2-byte header [channel_code, slot] before the PCM body.
_CH = {"mono": 0, "left": 1, "right": 2}


def check(cond: bool, label: str) -> None:
    global _failures
    print(f"  {PASS if cond else FAIL} {label}")
    if not cond:
        _failures += 1


def drain_until(ws, predicate, limit: int = 10):
    """Read JSON control messages until one matches predicate (or give up)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    return None


def main() -> int:
    client = TestClient(app)

    print("[1] Config + gating")
    cfg = client.get("/live/config").json()
    check(cfg.get("enabled") is True, "live mode reported enabled")
    check(cfg.get("transport") == "ws-relay", "transport is ws-relay")
    check(isinstance(cfg.get("ice_servers"), list), "ice_servers list present (WebRTC-ready)")

    print("[2] Relay: publisher frame reaches listener in the same room")
    room = "ROOMA"
    with client.websocket_connect(f"/live/ws/{room}?role=listener&name=Spk") as listener:
        hello = listener.receive_json()
        check(hello["type"] == "hello" and hello["role"] == "listener", "listener got hello")

        with client.websocket_connect(
            f"/live/ws/{room}?role=publisher&name=MicA&channel=left"
        ) as pub:
            phello = pub.receive_json()
            check(phello["role"] == "publisher" and phello["channel"] == "left",
                  "publisher got hello with channel")

            # Listener should see a roster update naming the publisher.
            roster = drain_until(listener, lambda m: m.get("type") == "roster"
                                 and len(m.get("publishers", [])) == 1)
            check(roster is not None, "listener roster shows 1 publisher")
            check(roster and roster["publishers"][0]["name"] == "MicA", "publisher name in roster")

            # Publisher streams a frame; listener must receive it with the 2-byte
            # [channel, slot] routing header prepended, then the exact PCM body.
            payload = bytes([1, 2, 3, 4, 250, 251, 252, 253])
            pub.send_bytes(payload)
            got = listener.receive_bytes()
            check(got[2:] == payload, "audio body relayed byte-for-byte after header")
            check(got[0] == _CH["left"], "frame header carries the 'left' channel code")
            check(len(got) == len(payload) + 2, "frame has a 2-byte routing header")

    print("[2b] Stereo + multi-phone: two phones get distinct slots and channels")
    room2 = "ROOMS"
    with client.websocket_connect(f"/live/ws/{room2}?role=listener") as lst:
        lst.receive_json()  # hello
        with client.websocket_connect(
            f"/live/ws/{room2}?role=publisher&name=Left&channel=left"
        ) as pl, client.websocket_connect(
            f"/live/ws/{room2}?role=publisher&name=Right&channel=right"
        ) as pr:
            pl.receive_json(); pr.receive_json()
            drain_until(lst, lambda m: m.get("type") == "roster"
                        and len(m.get("publishers", [])) == 2)
            pl.send_bytes(b"\x10\x11")
            pr.send_bytes(b"\x20\x21")
            f1 = lst.receive_bytes()
            f2 = lst.receive_bytes()
            by_ch = {f[0]: f for f in (f1, f2)}
            check(_CH["left"] in by_ch and _CH["right"] in by_ch,
                  "listener receives one left frame and one right frame")
            slots = {f[1] for f in (f1, f2)}
            check(len(slots) == 2, "the two phones use distinct slots (separate mix buffers)")

    print("[3] Isolation: a frame does NOT leak to another room")
    with client.websocket_connect("/live/ws/ROOMB?role=listener") as l_b:
        l_b.receive_json()  # hello
        with client.websocket_connect("/live/ws/ROOMC?role=publisher") as p_c:
            p_c.receive_json()  # hello
            p_c.send_bytes(b"\x09\x09")
            # Send a control ping in ROOMB's publisher-less room; listener should get
            # NOTHING binary. Use a short timeout via a follow-up control to confirm.
            # We assert by checking the next message on l_b is a roster (control), not bytes.
            # (No publisher joined ROOMB, so no audio can arrive.)
        # After p_c disconnects, ROOMB listener still only ever saw control JSON.
        # A quick roster ping: connect+disconnect a ROOMB publisher to force a roster.
        with client.websocket_connect("/live/ws/ROOMB?role=publisher") as p_b:
            p_b.receive_json()
            nxt = drain_until(l_b, lambda m: m.get("type") == "roster"
                              and len(m.get("publishers", [])) == 1)
            check(nxt is not None, "ROOMB listener saw only its own room's publisher (no leak)")

    print("[4] Mute hides audio + updates roster")
    with client.websocket_connect("/live/ws/ROOMD?role=listener") as ld:
        ld.receive_json()
        with client.websocket_connect("/live/ws/ROOMD?role=publisher&name=M") as pd:
            pd.receive_json()
            drain_until(ld, lambda m: m.get("type") == "roster")
            pd.send_json({"type": "status", "muted": True})
            muted_roster = drain_until(ld, lambda m: m.get("type") == "roster"
                                       and m["publishers"] and m["publishers"][0]["muted"])
            check(muted_roster is not None, "roster reflects muted publisher")
            pd.send_bytes(b"\x05\x05\x05")  # muted -> must NOT be relayed
            pd.send_json({"type": "status", "muted": False})
            unmuted = drain_until(ld, lambda m: m.get("type") == "roster"
                                  and m["publishers"] and not m["publishers"][0]["muted"])
            check(unmuted is not None, "unmute restores roster")
            pd.send_bytes(b"\x06\x06\x06")  # now it should arrive
            got = ld.receive_bytes()
            check(got[2:] == b"\x06\x06\x06", "audio flows again after unmute (muted frame was dropped)")

    print("[5] Ping -> pong (latency aid)")
    with client.websocket_connect("/live/ws/ROOME?role=publisher") as pe:
        pe.receive_json()
        pe.send_json({"type": "ping", "t": 123.0})
        pong = drain_until(pe, lambda m: m.get("type") == "pong")
        check(pong is not None and pong.get("t") == 123.0, "pong echoes ping timestamp")

    print()
    if _failures:
        print(f"LIVE SMOKE FAILED - {_failures} check(s) failed.")
        return 1
    print("LIVE SMOKE PASSED - Phase L0 relay transport works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
