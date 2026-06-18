"""Live Speaker mode HTTP + WebSocket endpoints (gated by ``live_mode_enabled``).

Transport (Phase L0): a WebSocket *relay* through the server. A publisher (phone
mic) sends opaque PCM frames; the server fans them out to every listener in the
same room. Because the relay runs on the public backend, it works on **any**
network the device can reach the server from — same Wi-Fi, phone hotspot, or
mobile 4G/5G — with no STUN/TURN setup. (A lower-latency WebRTC path can be added
later on the same signaling without changing the product flow.)

Nothing here touches the offline record→upload→process engine.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.live.registry import Channel, PeerRole, registry

router = APIRouter(prefix="/live", tags=["live"])

_STATIC_DIR = Path(__file__).resolve().parents[1] / "live_static"

# Audio frames may exceed the default WS text size; bytes are fine. Reject absurd
# frames so a misbehaving client can't exhaust memory (≈1s of 48k mono PCM16).
_MAX_FRAME_BYTES = 200_000


@router.get("/config")
def live_config() -> JSONResponse:
    """Client bootstrap: whether live mode is on + ICE servers for a future
    WebRTC upgrade (unused by the relay path, but lets the UI prepare)."""
    settings = get_settings()
    return JSONResponse(
        {
            "enabled": settings.live_mode_enabled,
            "transport": "ws-relay",
            "ice_servers": settings.live_ice_server_list,
        }
    )


@router.get("")
def live_index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@router.get("/publish")
def live_publish_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "publish.html"))


@router.get("/listen")
def live_listen_page() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "listen.html"))


@router.websocket("/ws/{room_code}")
async def live_ws(websocket: WebSocket, room_code: str) -> None:
    """One peer's connection to a live room.

    Query params: ``role`` (publisher|listener), ``name``, ``channel``
    (mono|left|right). Binary messages from a publisher are relayed to listeners;
    JSON messages are lightweight control/status (mute, level, latency ping).
    """
    settings = get_settings()
    if not settings.live_mode_enabled:
        await websocket.close(code=1008, reason="Live mode is disabled")
        return

    role_raw = (websocket.query_params.get("role") or "listener").lower()
    role: PeerRole = "publisher" if role_raw == "publisher" else "listener"
    name = (websocket.query_params.get("name") or "").strip()[:40]
    channel_raw = (websocket.query_params.get("channel") or "mono").lower()
    channel: Channel = channel_raw if channel_raw in ("mono", "left", "right") else "mono"

    await websocket.accept()
    peer = await registry.join(room_code, role, name, channel, websocket)

    # Tell the peer who it is, then refresh everyone's roster.
    await websocket.send_json(
        {"type": "hello", "peer_id": peer.id, "role": peer.role, "channel": peer.channel}
    )
    await registry.broadcast_roster(room_code)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                if peer.role != "publisher" or len(data) > _MAX_FRAME_BYTES:
                    continue
                await registry.relay_audio(room_code, peer, data)
                continue

            text = message.get("text")
            if text is not None:
                await _handle_control(room_code, peer, text)
    except WebSocketDisconnect:
        pass
    finally:
        await registry.leave(room_code, peer.id)
        await registry.broadcast_roster(room_code)


async def _handle_control(room_code: str, peer, text: str) -> None:
    """Handle a small JSON control message from a peer."""
    import json

    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return
    kind = msg.get("type")

    if kind == "ping":
        # Round-trip latency aid: echo the client's timestamp straight back.
        try:
            await peer.ws.send_json({"type": "pong", "t": msg.get("t")})
        except Exception:  # noqa: BLE001
            pass
        return

    if kind == "status":
        changed = False
        if "muted" in msg:
            peer.muted = bool(msg["muted"])
            changed = True
        if "level" in msg:
            try:
                peer.level = max(0.0, min(1.0, float(msg["level"])))
            except (ValueError, TypeError):
                pass
        if "latency_ms" in msg:
            try:
                peer.latency_ms = float(msg["latency_ms"])
            except (ValueError, TypeError):
                pass
        if "connection" in msg and msg["connection"] in ("stable", "weak", "lost"):
            peer.connection = msg["connection"]
            changed = True
        # Level updates are frequent; only re-broadcast the roster on real changes
        # (mute/connection), to keep control traffic light.
        if changed:
            await registry.broadcast_roster(room_code)
