# Live Speaker Mode — Testing Guide (Phase L0)

Phase L0 = **one (or more) phone microphones stream live to listeners** with no
upload/processing. Transport is a **WebSocket relay through the backend**, so it
works on **any network the phone can reach the server from**: same Wi‑Fi, phone
hotspot, or mobile **4G/5G** — with *no* STUN/TURN setup.

Live mode is **off by default** and completely separate from the offline
record → upload → process product. Turning it on changes nothing in the offline
flow.

---

## 0. Turn it on

**Local backend**

```powershell
cd MultiMicStudio\backend
$env:LIVE_MODE_ENABLED = 'true'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Railway (hosted)** — Service → Variables → add `LIVE_MODE_ENABLED = true` →
redeploy. Verify:

```
GET https://<your-app>.up.railway.app/health        -> live_mode: "on"
GET https://<your-app>.up.railway.app/live/config    -> {"enabled": true, ...}
```

Pages once enabled:

| Page | URL |
|------|-----|
| Index | `/live` |
| Microphone (publisher) | `/live/publish` |
| Listener (output) | `/live/listen` |

> Browsers only allow microphone capture over **HTTPS** (or `localhost`). On a
> phone use the Railway HTTPS URL, not a raw LAN IP.

---

## The test ladder

Climb one rung at a time. Each rung isolates one variable, so a failure tells you
exactly what broke.

### Rung 1 — Same machine (proves the pipeline)
1. Backend running locally with the flag on.
2. Browser tab A → `http://localhost:8000/live/listen`, room `TEST1`, **Start listening**.
3. Browser tab B → `http://localhost:8000/live/publish`, room `TEST1`, **Go live**, allow mic.
4. Talk into the computer mic → you hear it in tab A. Listener count = 1 on the
   publisher; microphone count = 1 on the listener.
   *Use headphones to avoid the speaker feeding back into the mic.*

### Rung 2 — Phone mic → PC listener, same Wi‑Fi
1. Deploy with the flag on (or expose local backend over HTTPS).
2. PC browser → `<https-url>/live/listen`, room `PARTY1`, **Start listening**.
3. Phone browser → `<https-url>/live/publish`, room `PARTY1`, **Go live**, allow mic.
4. Speak into the phone → PC plays it live. Watch **Latency** on the phone and
   **Buffer** on the PC.

### Rung 3 — Phone on **4G/5G** (cross‑network)
1. Turn **off Wi‑Fi** on the phone (use mobile data). PC stays on its own network.
2. Repeat Rung 2 with the same room code.
3. It still works because both sides reach the **public Railway server** — the
   relay needs no same‑network path and no TURN. Expect slightly higher latency.

### Rung 4 — Phone hotspot
1. PC joins the **phone's hotspot** (or a second phone listens).
2. Same room code. Confirms the hotspot path.

### Rung 5 — Bluetooth speaker output
1. Pair the **listener device** (PC or host phone) to a Bluetooth speaker in the
   OS settings first.
2. Open `/live/listen` and press **Start** — the live audio follows the system
   output to the Bluetooth speaker. (Bluetooth is output‑only here; the phone mic
   still streams over Wi‑Fi/4G.)

### Rung 6 — Live stereo (two phones)
1. Listener on PC, room `STEREO1`, **Start listening**.
2. Phone A → `/live/publish`, **Stereo side = Left** (follow the on‑screen
   placement guide — put it on the left).
3. Phone B → `/live/publish`, **Stereo side = Right** (place it on the right).
4. Both stream into the same room. The listener routes the **left** phone to the
   left speaker and the **right** phone to the right speaker, mixing them on a
   shared timeline. The roster shows an `L` / `R` tag per phone and the **Stereo**
   status reads **Strong** when both sides are live.

### Rung 7 — Several phones (party / room mode)
1. Listener on PC, room `PARTY9`.
2. Three or more phones → `/live/publish` (any mix of Left/Right/Mono), same room.
3. All phones mix together live. The listener roster shows each phone's state:
   `connected` / `streaming` / `muted` / `weak connection` / `high delay` /
   `disconnected`.

### Feedback / echo check (req H)
With a listener playing out loud near a publishing phone, raise the speaker volume
until it starts to ring. The publisher's **Feedback risk** flips to **High**, a
warning banner appears, and (if *Auto‑lower* is ticked) the mic gain ducks until it
clears. Move the phone away or use headphones to confirm it returns to **Low**.

---

## What the status fields mean

| Field | Where | Meaning |
|-------|-------|---------|
| Status | both | `LIVE` / `listening` = connected; `offline` = socket closed |
| Listeners | publisher | how many outputs are receiving |
| Microphones | listener | how many phones are streaming (with name + L/R tag) |
| Latency | both | rating word — **Excellent** / **Good** / **Too much delay** (+ ms) |
| Connection | publisher | **Stable** / **Weak** (from round‑trip time) |
| Feedback risk | publisher | **Low** / **High** — High warns + can auto‑lower the mic |
| Stereo | listener | **Strong** (both L+R live) / **Medium** (one side) / **Mono** |
| Mic level | publisher | live input meter (confirms the mic is actually capturing) |
| Per‑phone state | listener | `connected` / `streaming` / `muted` / `weak connection` / `high delay` / `disconnected` |

---

## Automated transport test (no devices)

Proves the relay routes audio publisher → listener, isolates rooms, honours mute,
and ping/pong works — entirely in‑process:

```powershell
cd MultiMicStudio\backend
.\.venv\Scripts\python.exe scripts\live_smoke.py     # -> LIVE SMOKE PASSED
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `/live/config` 404 | Live mode not enabled — set `LIVE_MODE_ENABLED=true` and redeploy. |
| "mic blocked" on phone | Not HTTPS, or permission denied. Use the HTTPS URL; allow the mic. |
| No sound, listener buffer climbing | Listener tab wasn't started with a user tap (audio needs a gesture) — press **Start listening** again. |
| Echo / squeal | Listener speaker is feeding the mic. Use headphones or move them apart (feedback protection is a later phase). |
| Choppy on 4G | Higher jitter — expected on L0; the WebRTC upgrade (later) lowers latency. |

---

## Where this sits in the roadmap

L0 (this) → **relay transport, 1+ phones → listeners, any network, status + mute.**
Next, on the *same* foundation: live stereo routing (L‑stereo) · feedback/echo
protection (H) · WebRTC low‑latency path + TURN config (already wired in
`live_ice_servers`) · multi‑phone room mix. Design context:
[ARCHITECTURE_HOSTING_AND_LIVE_MODE.md](ARCHITECTURE_HOSTING_AND_LIVE_MODE.md).
