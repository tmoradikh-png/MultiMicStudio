# MultiMic Studio — Hosting & Live Mode Architecture

Status: **#7 (hosting prep) implemented.** Live Mode is a **future milestone**: this
document is the design + plan so the hosting/storage work we ship now does **not**
block it. Nothing here changes the offline pipeline or the frozen audio baseline.

---

## 1. Two processing modes (one product, one codebase)

The product has **two processing modes** that share the same accounts, sessions,
participants, storage and quality bench:

### Mode 1 — Offline / Post-processing (shipped, frozen)
```
record → upload → align → mix → enhance → export
```
- High quality, deterministic, not latency-sensitive.
- This is the current MVP. It is **frozen** (tag `v0.1.0-audio-baseline`) and must
  not change. All of #7 is additive and leaves this path byte-for-byte identical
  when run with default settings.

### Mode 2 — Live / Real-time (future milestone)
```
capture small chunks → low-latency sync/align → continuous mix/enhance → fan-out
                                                                          ├─ speaker
                                                                          ├─ headphones/monitor
                                                                          ├─ live stream / broadcast
                                                                          └─ web dashboard playback
```
- Same enhancement *intent* (natural / studio_voice / karaoke / party) but a
  real-time implementation tuned for latency, not a re-use of the offline effects
  as-is (see §5).
- Gated behind `LIVE_MODE_ENABLED` (default **off**).

**Design rule:** Live Mode is a *parallel* path, never a rewrite of the offline
path. The offline pipeline remains the high-quality "master" render; Live Mode is
the low-latency monitor/broadcast render. A live session can still produce a
high-quality offline master afterwards from the same captured chunks.

---

## 2. What #7 ships now (hosting prep)

All opt-in; defaults reproduce the MVP exactly.

| Concern | Implementation | Default |
|---|---|---|
| Config | `app/config.py` env-driven (`DATABASE_URL`, `CORS_ORIGINS`, `PUBLIC_BASE_URL`, secrets) | unchanged |
| Persistent DB | `DATABASE_URL` already supports Postgres (`psycopg2`) | SQLite |
| Object storage | `app/storage.py` `S3Storage` (lazy `boto3`); works with S3 / R2 / Spaces / MinIO | `local` |
| Secure file URLs | HMAC-signed, time-limited links (`make_signed_query` / `verify_signature`); `Storage.signed_url()` | signing **off** |
| Gated serving | `main.py`: open static mount when signing off; signature-checked route when on | static mount |
| Absolute URLs | `PUBLIC_BASE_URL` prefixes stored links for a real domain/CDN | relative `/files/...` |
| Presented links | `projects.py:_present()` returns signed/presigned links in `/outputs` | identical to stored URL |

**Backward compatibility:** with the default `.env`, `signed_url == public_url`,
`PUBLIC_BASE_URL` is empty, storage is local, and `/files` is the same static
mount. The smoke tests pass unchanged.

### Recommended production topology (offline mode)
```
            ┌─────────────┐     ┌──────────────────┐
  phones ──▶│  API (FastAPI)│──▶│ Postgres          │
  web ─────▶│  stateless    │   └──────────────────┘
            │  N replicas   │     ┌──────────────────┐
            └──────┬────────┘────▶│ Object storage    │ (S3/R2)  → CDN + signed URLs
                   │              └──────────────────┘
                   ▼
            ┌─────────────┐     ┌──────────────────┐
            │ Job queue    │──▶ │ Offline workers   │ (mix/enhance/transcribe)
            │ (Redis/RQ)   │    └──────────────────┘
            └─────────────┘
```
- API is **stateless** → horizontal scale, rolling deploys.
- Processing already isolated behind `worker/tasks.py:dispatch_processing()`. Swap
  the body for a Redis/RQ/Celery enqueue with no call-site changes (already noted
  in code). **This queue seam is the same place Live workers attach** (§4).

---

## 3. Live Mode transport — WebRTC + WebSocket (recommended)

| Option | Verdict | Why |
|---|---|---|
| **WebRTC** (phone→server audio legs) | ✅ primary | Built-in **jitter buffer**, **Opus**, **RTP timestamps** for drift handling, NAT traversal, congestion control, echo cancel toggle. Purpose-built for real-time audio. |
| **WebSocket** (control + fallback audio) | ✅ secondary | Simple signaling/control channel (roles, take, mode switches, beep trigger, health). Can also carry Opus chunks as a degraded fallback when WebRTC can't connect. |
| **WHIP/WHEP or WebSocket** (server→stream out) | ✅ output | Standard ingest for the live stream / broadcast leg. |
| Raw WS PCM chunks only | ❌ not primary | No jitter buffer, no drift correction, no congestion control — we'd reinvent WebRTC, badly. Keep only as fallback. |

**Why WebRTC for the phone legs:** the hardest live problems — packet jitter, late
packets, and per-device **clock drift** — are exactly what RTP timestamps + the
WebRTC jitter buffer solve. Trying to do this over plain WebSocket means rebuilding
that machinery. Server side: a Python SFU/endpoint via **`aiortc`** (pure-Python,
fits the FastAPI process model) for the first version; move to a native SFU
(mediasoup/LiveKit/Janus) only if we outgrow it.

---

## 4. Live Mode server architecture (stateful, isolated)

```
 phone A ─WebRTC▶┐
 phone B ─WebRTC▶│   ┌────────────────────────────────────────────┐
                 ├──▶│  Live Session Worker (1 per active session) │
 control ─WS────▶┘   │  • per-track jitter buffer + RTP clock      │
                     │  • rolling sync/align (low-latency)         │
                     │  • continuous mix → live enhance (mode)     │
                     │  • ring buffer + N ms monitor delay         │
                     └───────────┬───────────────┬────────────────┘
                                 │               │
                       speaker / monitor   stream out (WHIP) / web (WebRTC/WS)
```

- **A `Live Session Worker` is a separate process/service**, not the stateless API.
  The API stays stateless and the **offline queue is never blocked** by real-time
  CPU. The worker attaches at the same logical seam as `dispatch_processing()`.
- One worker owns one live session's mixing state. Start with **pinning one session
  to one worker** (simplest correct model); scale out by sessions later.
- The worker can **also persist the raw chunks** to object storage so the same
  session yields a **high-quality offline master** afterward (reuses the frozen
  pipeline → best of both modes).

### Sync / drift / jitter handling
- **Anchor:** reuse the existing audible **sync beep** for a coarse common t0, then
  refine continuously with RTP timestamps + short rolling cross-correlation (the
  same envelope-correlation idea as offline, windowed for low latency).
- **Jitter buffer:** adaptive, target 40–80 ms; drop/conceal late packets rather
  than stalling the mix.
- **Drift:** resample per-track by tiny ratios from RTP-clock vs local-clock slope
  (a few ppm) so tracks stay aligned over long sessions.

### Latency targets (monitoring)
| Leg | Target |
|---|---|
| Mic → server (WebRTC) | 30–60 ms |
| Jitter buffer | 40–80 ms |
| Mix + live enhance | 5–20 ms/block |
| Server → monitor/stream | 30–60 ms |
| **End-to-end monitor** | **≈ 150–250 ms** (good for monitoring/broadcast; not for in-ear live performance, which needs <30 ms and is out of scope) |

---

## 5. CPU cost of live enhancement

The offline presets (`app/audio/effects.py`) are **block-at-once** (whole-file
`lfilter`/`sosfilt`, FFT reverb tails). For Live Mode they must be re-expressed as
**streaming, stateful** filters (carry filter state across blocks):

| Mode | Live cost | Notes |
|---|---|---|
| **live natural stereo** | Low | pan + level + light gate; ship first |
| **live studio_voice** | Medium | streaming HPF/EQ/denoise-gate/compressor (carry biquad + envelope state) |
| **live karaoke** | Medium–High | + streaming reverb/echo (ring-buffer comb/allpass) |
| **live party** | Medium–High | + M/S widening + reverb |

- Per active session, real-time DSP for 2 tracks at 48 kHz is comfortably within one
  CPU core; budget ~0.1–0.3 core/session for studio_voice, more for karaoke/party.
- **The offline effects stay untouched.** Live filters live in a new module
  (e.g. `app/audio/live_effects.py`) so the frozen baseline is never edited.

---

## 6. Rollout scope & fallback

- **Start with 1 host + 1 guest** (2 tracks). It exercises transport, sync, drift,
  mixing and one output leg without SFU fan-in complexity. Expand to N later.
- **Mode order:** live natural → live studio_voice → live karaoke/party.
- **Fallback to offline if the network is unstable:** the client always keeps
  recording locally (it already does for offline). If WebRTC degrades past a
  threshold, the UI drops the live monitor and guarantees the session still
  produces the high-quality offline master from the uploaded chunks. **Live is
  best-effort; offline is the guarantee.**

---

## 7. Why #7 does not block Live Mode

| Live Mode needs | Already provided by #7 |
|---|---|
| Stateful real-time workers without starving offline jobs | API is stateless; processing is isolated behind the `dispatch_processing()` queue seam — live workers attach at the same seam |
| Durable storage for chunks + masters | Object-storage backend (`S3Storage`) + signed URLs |
| Secure, time-limited media links for stream/monitor consumers | `signed_url()` / presigned URLs |
| Per-environment toggles | `LIVE_MODE_ENABLED` + env config |
| Real domain / CDN addressing for output legs | `PUBLIC_BASE_URL` |
| Same identities for host/guest in live | existing accounts + guest-token participants reused |

No schema migration is forced now. When Live Mode starts, add a `processing_mode`
field to sessions and the live tables — additive, exactly like the guest-token
migration pattern already in `database.py:_ensure_columns()`.

---

## 8. Phased plan & estimate (Live Mode milestone)

> Indicative engineering effort, assuming the offline product stays frozen.

| Phase | Scope | Est. |
|---|---|---|
| **L0 — Spike** | `aiortc` 1 phone → server, receive Opus, write WAV; validate RTP timestamps + jitter buffer on real phones | 3–5 days |
| **L1 — 1 host + 1 guest live** | 2 WebRTC legs + WS control; beep anchor + rolling align; **live natural stereo** mix; monitor leg back to web | 1.5–2.5 weeks |
| **L2 — Live studio_voice** | streaming/stateful studio_voice in `live_effects.py`; A/B vs offline; CPU budgeting | 1–1.5 weeks |
| **L3 — Stream-out + fallback** | WHIP/WS broadcast leg; instability detection → graceful offline fallback; persist chunks → offline master | 1.5–2 weeks |
| **L4 — karaoke/party + scale** | streaming reverb/echo/widen; >2 participants; session→worker scheduling | 2–3 weeks |

**Recommendation:** schedule **L0 spike** before committing to the milestone (it
de-risks transport, sync and drift on real devices). Ship offline-first product to
private beta now; begin L0 in parallel only if there's appetite. Nothing in #7
needs to change to start L0.
