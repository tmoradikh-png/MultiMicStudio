"""In-memory registry of live rooms and their connected peers.

A *room* is a live session (identified by a short code the host shares). A *peer*
is one WebSocket connection in that room, either:

  * ``publisher`` — a phone microphone streaming audio into the room, or
  * ``listener``  — an output device (web/PC/host phone/Bluetooth speaker) playing
    the room's audio live.

The registry only *routes* opaque audio frames (publisher → listeners) and small
JSON control messages; it never decodes or processes audio. This keeps the live
path light and ensures it cannot affect the offline audio engine.

State is process-local. For the single-replica private beta that is sufficient; a
multi-replica deployment would move this to Redis/pub-sub (see the hosting
architecture doc), which is why all access goes through this one class.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal

from fastapi import WebSocket

PeerRole = Literal["publisher", "listener"]
Channel = Literal["mono", "left", "right"]

# Channel codes carried in the 2-byte frame header (see ``relay_audio``). Kept in
# sync with the listener page's parser.
_CHANNEL_CODE: dict[str, int] = {"mono": 0, "left": 1, "right": 2}


@dataclass
class Peer:
    """One WebSocket connection in a room."""

    id: str
    role: PeerRole
    name: str
    channel: Channel
    ws: WebSocket
    joined_at: float = field(default_factory=time.time)
    # Stable small integer (0..255) identifying a publisher inside its room. Sent
    # in every audio frame header so the listener can keep one jitter buffer per
    # phone and mix several phones together (req F — multi-phone live mode).
    slot: int = 0
    # Lightweight live status surfaced in the roster (req G — status display).
    muted: bool = False
    level: float = 0.0  # 0..1 recent mic level (publishers only)
    latency_ms: float | None = None
    connection: str = "stable"  # stable | weak | lost

    def public(self) -> dict:
        """Roster-safe view (no WebSocket handle)."""
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "channel": self.channel,
            "slot": self.slot,
            "muted": self.muted,
            "level": round(self.level, 3),
            "latency_ms": self.latency_ms,
            "connection": self.connection,
        }


@dataclass
class Room:
    code: str
    peers: dict[str, Peer] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def publishers(self) -> list[Peer]:
        return [p for p in self.peers.values() if p.role == "publisher"]

    def listeners(self) -> list[Peer]:
        return [p for p in self.peers.values() if p.role == "listener"]

    def roster(self) -> dict:
        return {
            "type": "roster",
            "room": self.code,
            "publishers": [p.public() for p in self.publishers()],
            "listeners": [p.public() for p in self.listeners()],
        }


class LiveRegistry:
    """Process-local rooms with async-safe join/leave/broadcast."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def join(
        self,
        room_code: str,
        role: PeerRole,
        name: str,
        channel: Channel,
        ws: WebSocket,
    ) -> Peer:
        async with self._lock:
            room = self._rooms.get(room_code)
            if room is None:
                room = Room(code=room_code)
                self._rooms[room_code] = room
            peer = Peer(
                id=secrets.token_urlsafe(8),
                role=role,
                name=name or ("Mic" if role == "publisher" else "Listener"),
                channel=channel,
                ws=ws,
            )
            if role == "publisher":
                used = {p.slot for p in room.publishers()}
                slot = 0
                while slot in used and slot < 255:
                    slot += 1
                peer.slot = slot
            room.peers[peer.id] = peer
            return peer

    async def leave(self, room_code: str, peer_id: str) -> None:
        async with self._lock:
            room = self._rooms.get(room_code)
            if room is None:
                return
            room.peers.pop(peer_id, None)
            if not room.peers:
                self._rooms.pop(room_code, None)

    def get_room(self, room_code: str) -> Room | None:
        return self._rooms.get(room_code)

    def _targets(self, room_code: str, role: PeerRole) -> list[Peer]:
        room = self._rooms.get(room_code)
        if room is None:
            return []
        # Snapshot so a concurrent disconnect can't mutate the set mid-iteration.
        return [p for p in list(room.peers.values()) if p.role == role]

    async def relay_audio(self, room_code: str, sender: Peer, frame: bytes) -> int:
        """Forward one opaque audio frame from a publisher to every listener.

        A 2-byte routing header ``[channel_code, slot]`` is prepended so the
        listener can pan the frame (mono/left/right — req E live stereo) and keep a
        separate jitter buffer per phone to mix several phones (req F). The PCM
        payload itself is never decoded. The 2-byte header also keeps the Int16 PCM
        body 2-byte aligned for the browser's ``Int16Array`` view.

        Returns the number of listeners the frame was delivered to. Dead sockets are
        ignored (the peer's own receive loop cleans them up on disconnect).
        """
        if sender.muted:
            return 0
        header = bytes((_CHANNEL_CODE.get(sender.channel, 0), sender.slot & 0xFF))
        out = header + frame
        delivered = 0
        for listener in self._targets(room_code, "listener"):
            try:
                await listener.ws.send_bytes(out)
                delivered += 1
            except Exception:  # noqa: BLE001 — drop on broken socket, keep streaming
                continue
        return delivered

    async def broadcast_control(self, room_code: str, message: dict) -> None:
        """Send a JSON control message to every peer in the room."""
        room = self._rooms.get(room_code)
        if room is None:
            return
        for peer in list(room.peers.values()):
            try:
                await peer.ws.send_json(message)
            except Exception:  # noqa: BLE001
                continue

    async def broadcast_roster(self, room_code: str) -> None:
        room = self._rooms.get(room_code)
        if room is None:
            return
        await self.broadcast_control(room_code, room.roster())


# Single shared registry for the process.
registry = LiveRegistry()
