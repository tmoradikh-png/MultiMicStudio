#!/usr/bin/env python3
"""Remote end-to-end smoke test against a DEPLOYED MultiMic Studio backend.

Unlike smoke_test.py / beta_readiness.py (which run the app in-process via
TestClient), this script talks to a live HTTPS backend over HTTP, so it validates
the real hosted stack: Postgres, S3-compatible storage, signed file URLs, HTTPS.

It does NOT change any product or audio behaviour — it only drives the public API
the same way a host phone + a no-account guest phone would.

Usage:
    python scripts/remote_smoke.py https://<your-service>.up.railway.app

Flow:
    /health  ->  host signup  ->  create session  ->  guest join (no account)
    ->  start  ->  both phones upload a short WAV (shared clap for alignment)
    ->  stop  ->  process  ->  poll /projects/{id}/outputs until done
    ->  assert the 7 outputs are present and their (signed) URLs are fetchable.

Only depends on numpy + soundfile (already in the backend venv) and the stdlib —
no `requests` needed.
"""
from __future__ import annotations

import io
import json
import sys
import time
import uuid
import urllib.error
import urllib.request

import numpy as np
import soundfile as sf

TIMEOUT = 30
PROCESS_TIMEOUT_S = 180
SR = 44100


# --------------------------------------------------------------------------- #
# Tiny HTTP helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _request(method, url, *, token=None, json_body=None, multipart=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if multipart is not None:
        boundary = "----multimic" + uuid.uuid4().hex
        data = _encode_multipart(multipart, boundary)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"detail": body}
        return exc.code, parsed


def _encode_multipart(fields, boundary):
    """fields: list of (name, value) for text or (name, filename, bytes) for files."""
    out = io.BytesIO()
    for field in fields:
        out.write(f"--{boundary}\r\n".encode())
        if len(field) == 2:
            name, value = field
            out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            out.write(f"{value}\r\n".encode())
        else:
            name, filename, content = field
            out.write(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            out.write(b"Content-Type: audio/wav\r\n\r\n")
            out.write(content)
            out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue()


def _fetch_ok(url):
    """HEAD/GET a (possibly signed) file URL and confirm it returns 2xx."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 300
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Audio fixture: a short clip with a shared clap so two phones align.
# --------------------------------------------------------------------------- #
def _make_wav(tone_hz: float) -> bytes:
    dur = 3.0
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    sig = 0.2 * np.sin(2 * np.pi * tone_hz * t)
    # Shared transient (clap) at 0.5s — identical on both phones for cross-correlation.
    clap_start = int(0.5 * SR)
    clap = np.hanning(int(0.02 * SR))
    sig[clap_start : clap_start + clap.size] += 0.9 * clap
    sig = np.clip(sig, -1.0, 1.0).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, sig, SR, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
def main(base: str) -> int:
    base = base.rstrip("/")
    fails: list[str] = []

    def check(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    print(f"Remote smoke test against {base}\n")

    # 1) Health -------------------------------------------------------------- #
    print("[1] Health")
    status, body = _request("GET", f"{base}/health")
    check("GET /health is 200", status == 200, f"status={status}")
    check("status ok", body.get("status") == "ok", json.dumps(body))
    check("Live Mode is off", body.get("live_mode") == "off", str(body.get("live_mode")))
    print(f"      storage backend reported: {body.get('storage')!r}")

    # 2) Host signs up + creates a session ---------------------------------- #
    print("[2] Host signup + create session")
    email = f"smoke+{uuid.uuid4().hex[:10]}@example.com"
    status, body = _request(
        "POST", f"{base}/auth/signup",
        json_body={"name": "Smoke Host", "email": email, "password": "smoke-pass-123"},
    )
    check("signup 201", status == 201, f"status={status} {body}")
    host_token = body.get("access_token")
    check("got host token", bool(host_token))
    if not host_token:
        return _report(fails)

    status, body = _request(
        "POST", f"{base}/sessions", token=host_token, json_body={"title": "Remote smoke"}
    )
    check("create session 201", status == 201, f"status={status} {body}")
    session_id = body.get("id")
    code = body.get("code")
    # host participant is the first participant
    host_pid = body.get("participants", [{}])[0].get("id") if body.get("participants") else None
    check("got session id + code", bool(session_id and code), f"id={session_id} code={code}")
    if not (session_id and code):
        return _report(fails)

    # 3) Guest joins with NO account ---------------------------------------- #
    print("[3] Guest join (no account)")
    status, body = _request(
        "POST", f"{base}/sessions/join",
        json_body={"code": code, "speaker_name": "", "role": "speaker_mic"},
    )
    check("guest join 200", status == 200, f"status={status} {body}")
    guest_token = body.get("guest_token")
    guest_pid = body.get("participant", {}).get("id")
    check("guest got token + participant", bool(guest_token and guest_pid))

    # host participant id fallback: refetch session
    if not host_pid:
        status, sess = _request("GET", f"{base}/sessions/{session_id}", token=host_token)
        for p in sess.get("participants", []):
            if p.get("role") == "host":
                host_pid = p.get("id")
    check("have host participant id", bool(host_pid))
    if not (guest_token and guest_pid and host_pid):
        return _report(fails)

    # 4) Start ------------------------------------------------------------- #
    print("[4] Start recording")
    status, body = _request("POST", f"{base}/sessions/{session_id}/start", token=host_token)
    check("start 200", status == 200, f"status={status} {body}")
    take_id = body.get("current_take_id")

    # 5) Both phones upload a short WAV ------------------------------------- #
    print("[5] Uploads (host + guest)")
    host_wav = _make_wav(180.0)
    guest_wav = _make_wav(240.0)

    def upload(token, pid, wav, who):
        fields = [
            ("session_id", session_id),
            ("participant_id", pid),
            ("duration_seconds", "3.0"),
            ("sample_rate", str(SR)),
        ]
        if take_id:
            fields.append(("take_id", take_id))
        fields.append(("file", f"{who}.wav", wav))
        st, bd = _request("POST", f"{base}/recordings", token=token, multipart=fields)
        check(f"{who} upload 201", st == 201, f"status={st} {bd}")

    upload(host_token, host_pid, host_wav, "host")
    upload(guest_token, guest_pid, guest_wav, "guest")

    # 6) Stop -------------------------------------------------------------- #
    print("[6] Stop recording")
    status, body = _request("POST", f"{base}/sessions/{session_id}/stop", token=host_token)
    check("stop 200", status == 200, f"status={status} {body}")

    # 7) Process + poll outputs -------------------------------------------- #
    print("[7] Process + poll outputs")
    status, body = _request("POST", f"{base}/projects/process/{session_id}", token=host_token)
    check("process accepted (202)", status == 202, f"status={status} {body}")

    outputs = None
    deadline = time.time() + PROCESS_TIMEOUT_S
    last = None
    while time.time() < deadline:
        st, bd = _request("GET", f"{base}/projects/{session_id}/outputs", token=host_token)
        last = (st, bd)
        if st == 200 and bd.get("processing_status") == "done":
            outputs = bd
            break
        time.sleep(3)
    check("processing reached done", outputs is not None, f"last={last}")
    if outputs is None:
        return _report(fails)

    items = outputs.get("outputs", [])
    roles = {o.get("role"): o for o in items}
    expected = {
        "raw_phone_1", "raw_phone_2", "natural_stereo",
        "studio_voice", "karaoke", "party", "mono_downmix",
    }
    present = {r for r in expected if roles.get(r, {}).get("available")}
    check("all 7 output roles available", present == expected, f"missing={expected - present}")

    badge = outputs.get("quality") or {}
    print(f"      quality badge: ok={badge.get('ok')} "
          f"{badge.get('passed')}/{badge.get('total')} checks, "
          f"baseline_failed={badge.get('baseline_failed')}")

    # 8) Signed URLs are actually fetchable -------------------------------- #
    print("[8] Fetch signed file URLs")
    natural = roles.get("natural_stereo", {}).get("url")
    check("natural stereo URL present", bool(natural))
    if natural:
        full = natural if natural.startswith("http") else base + natural
        check("natural stereo URL fetchable", _fetch_ok(full), full[:80])

    return _report(fails)


def _report(fails: list[str]) -> int:
    print()
    if fails:
        print(f"REMOTE SMOKE FAILED — {len(fails)} check(s): {', '.join(fails)}")
        return 1
    print("REMOTE SMOKE PASSED — hosted backend is externally testable.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/remote_smoke.py https://<hosted-backend-url>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
