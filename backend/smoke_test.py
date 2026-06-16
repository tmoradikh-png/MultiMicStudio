"""End-to-end smoke test of the MVP pipeline using FastAPI's TestClient.

Creates a user + session, uploads two synthetic WAV files that share a clap marker,
runs processing (align + mix + stub transcript), and asserts a final mixed file and
transcript are produced. Requires FFmpeg on PATH.

Run:  .venv/Scripts/python.exe smoke_test.py
"""
import io
import uuid
import wave

import numpy as np

from fastapi.testclient import TestClient

from app.main import app

SR = 48_000


def make_wav(clap_at_s: float, length_s: float = 3.0, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    n = int(length_s * SR)
    sig = (rng.standard_normal(n) * 0.02).astype(np.float32)  # quiet noise floor
    clap = int(clap_at_s * SR)
    # Sharp transient = the "clap".
    sig[clap : clap + 200] += np.hanning(200).astype(np.float32)
    pcm = np.clip(sig, -1, 1)
    pcm16 = (pcm * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def main() -> None:
    # Use the context manager so the app lifespan (init_db) runs and tables exist.
    with TestClient(app) as client:
        _run(client)


def _run(client: TestClient) -> None:
    # 1. Signup (unique email so the test is rerunnable without DB cleanup)
    email = f"smoke+{uuid.uuid4().hex[:8]}@test.dev"
    r = client.post(
        "/auth/signup",
        json={"email": email, "name": "Smoke", "password": "pw12345"},
    )
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create session
    r = client.post("/sessions", json={"title": "Smoke session"}, headers=headers)
    assert r.status_code == 201, r.text
    session = r.json()
    session_id = session["id"]
    host = session["participants"][0]

    # 3. A second participant joins
    r = client.post(
        "/sessions/join",
        json={"code": session["code"], "speaker_name": "Guest"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    guest = r.json()["participant"]

    # 4. Upload two recordings with the clap at different positions (simulating offset)
    for participant, clap, seed in ((host, 0.5, 1), (guest, 0.9, 2)):
        wav = make_wav(clap_at_s=clap, seed=seed)
        r = client.post(
            "/recordings",
            data={
                "session_id": session_id,
                "participant_id": participant["id"],
                "sample_rate": str(SR),
                "duration_seconds": "3.0",
            },
            files={"file": (f"{participant['id']}.wav", wav, "audio/wav")},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    # 5. Process (synchronous in MVP via BackgroundTasks; TestClient runs them)
    r = client.post(f"/projects/process/{session_id}", headers=headers)
    assert r.status_code == 202, r.text

    # 6. Fetch project
    r = client.get(f"/projects/{session_id}", headers=headers)
    assert r.status_code == 200, r.text
    project = r.json()
    assert project["processing_status"] == "done", project
    assert project["final_audio_url"], "no final audio produced"
    assert project["transcript_text"], "no transcript produced"

    # 7. Final mixed file is downloadable
    r = client.get(project["final_audio_url"])
    assert r.status_code == 200, r.text
    assert len(r.content) > 1000, "mixed file suspiciously small"

    print("SMOKE TEST PASSED")
    print("  final_audio_url:", project["final_audio_url"])
    print("  transcript:", project["transcript_text"][:80], "...")

    _test_single_phone_and_dedup(client, headers)
    _test_guest_no_account_flow(client, headers)


def _wav_duration_seconds(content: bytes) -> float:
    with wave.open(io.BytesIO(content), "rb") as w:
        return w.getnframes() / w.getframerate()


def _test_single_phone_and_dedup(client: TestClient, headers: dict) -> None:
    """Verify (a) one-phone mix ~= raw length, and (b) retry uploads are deduped."""
    # Create a fresh single-phone session.
    r = client.post("/sessions", json={"title": "Solo session"}, headers=headers)
    assert r.status_code == 201, r.text
    session = r.json()
    session_id = session["id"]
    host = session["participants"][0]

    raw = make_wav(clap_at_s=0.4, length_s=4.0, seed=7)
    raw_dur = _wav_duration_seconds(raw)

    # Upload the SAME participant twice (simulating a retry) — must be deduped to one.
    for _ in range(2):
        r = client.post(
            "/recordings",
            data={
                "session_id": session_id,
                "participant_id": host["id"],
                "sample_rate": str(SR),
                "duration_seconds": str(raw_dur),
            },
            files={"file": (f"{host['id']}.wav", raw, "audio/wav")},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    r = client.post(f"/projects/process/{session_id}", headers=headers)
    assert r.status_code == 202, r.text

    r = client.get(f"/projects/{session_id}", headers=headers)
    assert r.status_code == 200, r.text
    project = r.json()
    assert project["processing_status"] == "done", project

    mixed = client.get(project["final_audio_url"]).content
    mixed_dur = _wav_duration_seconds(mixed)

    # Single phone after dedup: mix length must match the raw (no duplication/stack).
    assert abs(mixed_dur - raw_dur) < 0.1, (
        f"single-phone mix duration {mixed_dur:.2f}s != raw {raw_dur:.2f}s "
        "(duplication/stacking bug)"
    )

    print("SINGLE-PHONE + DEDUP TEST PASSED")
    print(f"  raw={raw_dur:.2f}s mixed={mixed_dur:.2f}s (deduped 2 uploads -> 1)")

    _test_alignment_accuracy()
    _test_multi_take_isolation(client, headers)
    _test_enhancement_presets()


def _upload(client, headers, session_id, participant_id, take_id, wav, dur):
    r = client.post(
        "/recordings",
        data={
            "session_id": session_id,
            "participant_id": participant_id,
            "take_id": take_id,
            "sample_rate": str(SR),
            "duration_seconds": str(dur),
        },
        files={"file": (f"{participant_id}.wav", wav, "audio/wav")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _wav_channels(content: bytes) -> int:
    with wave.open(io.BytesIO(content), "rb") as w:
        return w.getnchannels()


def _test_multi_take_isolation(client: TestClient, headers: dict) -> None:
    """Record 3 takes in ONE login; each mix must use only its own take's audio.

    Reproduces the reported bug: take 1 synced, take 2 lost sync because old state
    leaked. With take_id tagging, processing must select exactly one recording per
    participant for the LATEST take only.
    """
    from app.database import SessionLocal
    from app.worker.tasks import _select_input_recordings

    r = client.post("/sessions", json={"title": "Multi-take"}, headers=headers)
    assert r.status_code == 201, r.text
    session = r.json()
    session_id = session["id"]
    host = session["participants"][0]

    r = client.post(
        "/sessions/join",
        json={"code": session["code"], "speaker_name": "Guest"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    guest = r.json()["participant"]

    # Three takes with different marker positions and lengths.
    take_specs = [
        (0.5, 0.9, 4.0),  # take 1
        (0.6, 1.1, 5.0),  # take 2
        (0.4, 0.8, 6.0),  # take 3
    ]
    for i, (host_clap, guest_clap, length) in enumerate(take_specs, start=1):
        # Host Start mints a new take id.
        r = client.post(f"/sessions/{session_id}/start", headers=headers)
        assert r.status_code == 200, r.text
        take_id = r.json()["current_take_id"]
        assert take_id, "start did not mint a take id"

        _upload(
            client, headers, session_id, host["id"], take_id,
            make_wav(host_clap, length_s=length, seed=10 + i), length,
        )
        _upload(
            client, headers, session_id, guest["id"], take_id,
            make_wav(guest_clap, length_s=length, seed=20 + i), length,
        )

        # Selection must isolate THIS take: exactly 2 recordings, both tagged take_id.
        db = SessionLocal()
        try:
            selected = _select_input_recordings(db, session_id)
        finally:
            db.close()
        assert len(selected) == 2, (
            f"take {i}: expected 2 inputs, got {len(selected)} "
            "(old-take audio leaked into the mix)"
        )
        assert all(r.take_id == take_id for r in selected), (
            f"take {i}: selection included recordings from another take"
        )

        client.post(f"/sessions/{session_id}/stop", headers=headers)
        r = client.post(f"/projects/process/{session_id}", headers=headers)
        assert r.status_code == 202, r.text
        project = client.get(f"/projects/{session_id}", headers=headers).json()
        assert project["processing_status"] == "done", project

        mixed_dur = _wav_duration_seconds(
            client.get(project["final_audio_url"]).content
        )
        # Mix length ~= this take's length plus at most the alignment shift (the
        # marker offset, < ~1s here). It must NOT grow toward the sum of all takes,
        # which is what happened when old recordings leaked in.
        assert length - 0.2 <= mixed_dur <= length + 1.0, (
            f"take {i}: mix {mixed_dur:.2f}s outside expected ~{length:.2f}s "
            "(old recordings leaked / stacked)"
        )

    # Stereo + stems from the final take.
    assert project["final_audio_stereo_url"], "no stereo export produced"
    stereo = client.get(project["final_audio_stereo_url"]).content
    assert _wav_channels(stereo) == 2, "stereo export is not 2-channel"
    assert len(project["stems"]) == 2, (
        f"expected 2 participant stems, got {len(project['stems'])}"
    )

    print("MULTI-TAKE ISOLATION TEST PASSED")
    print("  3 takes recorded in one login; each mix used only its own take.")
    print("  STEREO + STEMS TEST PASSED (2-channel stereo + 2 stems).")


def _test_alignment_accuracy() -> None:
    """Verify two tracks with a marker at KNOWN different positions get aligned.

    This is the test that would have caught the inverted-sign bug: it asserts the
    markers coincide on the shared timeline to within ~50 ms after compute_offsets.
    """
    from app.audio.processing import (
        LoadedTrack,
        compute_offsets,
        detect_marker_offset,
    )

    def make_track(rid: str, marker_at_s: float, seed: int) -> LoadedTrack:
        rng = np.random.default_rng(seed)
        n = int(4.0 * SR)
        sig = (rng.standard_normal(n) * 0.02).astype(np.float32)
        m = int(marker_at_s * SR)
        sig[m : m + 200] += np.hanning(200).astype(np.float32)
        return LoadedTrack(
            recording_id=rid,
            samples=np.clip(sig, -1, 1).astype(np.float32),
            sample_rate=SR,
            local_start_ms=None,
        )

    # Reference marker at 0.50s, the other at 1.30s (0.80s later).
    ref = make_track("ref", 0.50, seed=11)
    other = make_track("other", 1.30, seed=12)
    tracks = [ref, other]

    offsets = compute_offsets(tracks)
    markers = {t.recording_id: detect_marker_offset(t.samples, SR) for t in tracks}
    assert markers["ref"] is not None, "reference marker not detected"
    assert markers["other"] is not None, "other marker not detected"

    # Marker position on the shared timeline = offset + marker-in-file.
    ref_pos = offsets["ref"] + markers["ref"]
    other_pos = offsets["other"] + markers["other"]
    delta_ms = abs(ref_pos - other_pos) / SR * 1000
    assert delta_ms <= 50, (
        f"markers misaligned by {delta_ms:.1f}ms after alignment "
        "(sync/sign bug). Expected <= 50ms."
    )

    print("ALIGNMENT ACCURACY TEST PASSED")
    print(f"  markers aligned within {delta_ms:.1f}ms (<= 50ms)")

    _test_alignment_no_marker()


def _test_alignment_no_marker() -> None:
    """Real-device case: two phones share room audio but have NO clean beep.

    Each phone hears the same conversation at a different offset, plus its own
    independent mic noise and a different level (closer/farther). There is no usable
    marker, so alignment must rely on the robust envelope/cross-correlation path.
    This guards against the "big delay / not synced" regression on real hardware.
    """
    from app.audio.processing import LoadedTrack, compute_offsets

    rng = np.random.default_rng(99)
    base_len = int(8.0 * SR)
    # A shared, speech-like loudness pattern: bursts of energy (words) and gaps.
    shared = np.zeros(base_len, dtype=np.float32)
    for start in rng.integers(0, base_len - SR, size=12):
        dur = int(rng.uniform(0.15, 0.4) * SR)
        seg = rng.standard_normal(dur).astype(np.float32)
        shared[start : start + dur] += seg
    shared *= 0.3

    true_offset_s = 0.62  # phone B started ~0.62 s after phone A
    off = int(true_offset_s * SR)

    def phone(level: float, noise: float, delay: int, seed: int) -> LoadedTrack:
        rng2 = np.random.default_rng(seed)
        n = base_len + off
        sig = rng2.standard_normal(n).astype(np.float32) * noise
        # Place the shared conversation starting at `delay` samples in this phone.
        sig[delay : delay + base_len] += shared * level
        return LoadedTrack(
            recording_id="A" if delay == 0 else "B",
            samples=np.clip(sig, -1, 1).astype(np.float32),
            sample_rate=SR,
            local_start_ms=None,
        )

    a = phone(level=1.0, noise=0.02, delay=0, seed=1)
    b = phone(level=0.6, noise=0.05, delay=off, seed=2)  # quieter + noisier + later
    offsets = compute_offsets([a, b])

    # After alignment the shared audio must line up: the magnitude of the relative
    # shift between the two placements must equal the true offset between them.
    rel = abs(offsets["A"] - offsets["B"])
    delta_ms = abs(rel - off) / SR * 1000
    assert delta_ms <= 60, (
        f"no-marker alignment off by {delta_ms:.1f}ms (expected <= 60ms) — "
        "robust envelope/xcorr path failed (the real-device 'big delay' bug)."
    )

    print("NO-MARKER (REAL-DEVICE) ALIGNMENT TEST PASSED")
    print(f"  shared-audio alignment within {delta_ms:.1f}ms (<= 60ms)")


def _test_enhancement_presets() -> None:
    """Each enhancement preset must:
      * return a 2-channel (N, 2) array,
      * keep EXACTLY the same length as the input (no stacking / drift),
      * preserve stereo separation (L and R stay distinct, not collapsed to mono).
    """
    import numpy as np

    from app.audio import effects

    sr = 48_000
    n = sr * 3  # 3 seconds
    t = np.arange(n) / sr
    # Distinct L/R content so a mono-collapse would be obvious:
    # left = 220 Hz tone, right = 440 Hz tone.
    left = 0.3 * np.sin(2 * np.pi * 220.0 * t)
    right = 0.3 * np.sin(2 * np.pi * 440.0 * t)
    stereo = np.stack([left, right], axis=1).astype(np.float32)

    def _corr(x: np.ndarray) -> float:
        a = x[:, 0] - x[:, 0].mean()
        b = x[:, 1] - x[:, 1].mean()
        denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
        return float(np.dot(a, b) / denom) if denom > 0 else 1.0

    for mode in effects.ENHANCEMENT_MODES:
        out = effects.apply_enhancement(stereo, sr, mode)
        assert out.ndim == 2 and out.shape[1] == 2, f"{mode}: not stereo {out.shape}"
        assert out.shape[0] == stereo.shape[0], (
            f"{mode}: length changed {stereo.shape[0]} -> {out.shape[0]} "
            "(would stack/drift)"
        )
        assert np.isfinite(out).all(), f"{mode}: non-finite samples"
        # Channels must remain distinct — not collapsed to a single mono signal.
        chan_corr = _corr(out)
        assert chan_corr < 0.99, (
            f"{mode}: stereo collapsed to mono (L/R correlation {chan_corr:.3f})"
        )

    print("ENHANCEMENT PRESETS TEST PASSED")
    print(f"  modes={list(effects.ENHANCEMENT_MODES)} preserve length + stereo")


def _test_guest_no_account_flow(client: TestClient, host_headers: dict) -> None:
    """No-account guests join a host's session, poll, upload, retry — no duplicates.

    Covers the private-beta requirement that phones can join with only a code/QR:
      * each guest gets its OWN anonymous guest_token (device identity),
      * the token alone authorizes status polling + upload (no login),
      * an upload retry with the same token reuses the same participant, so the
        mix is deduped to one recording per guest (no stacked/duplicate audio),
      * a guest cannot upload for another guest's participant,
      * the host still sees every guest in the participant list.
    """
    from app.database import SessionLocal
    from app.worker.tasks import _select_input_recordings

    # Host (logged-in) creates the session everyone joins by code.
    r = client.post("/sessions", json={"title": "Guest session"}, headers=host_headers)
    assert r.status_code == 201, r.text
    session = r.json()
    session_id = session["id"]
    code = session["code"]

    # Two phones join with NO Authorization header at all.
    def join_guest(name: str) -> dict:
        r = client.post("/sessions/join", json={"code": code, "speaker_name": name})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["guest_token"], "guest join did not return a guest_token"
        return body

    guest_a = join_guest("Phone A")
    guest_b = join_guest("Phone B")
    token_a = guest_a["guest_token"]
    token_b = guest_b["guest_token"]
    part_a = guest_a["participant"]["id"]
    part_b = guest_b["participant"]["id"]
    assert token_a != token_b, "two guests must get distinct tokens"
    assert part_a != part_b, "two guests must be distinct participants"

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Reconnect (app restart): re-joining with the SAME token returns the SAME
    # participant and token — no duplicate phone is created.
    r = client.post(
        "/sessions/join",
        json={"code": code, "speaker_name": "Phone A"},
        headers=headers_a,
    )
    assert r.status_code == 200, r.text
    reconnect = r.json()
    assert reconnect["participant"]["id"] == part_a, "reconnect made a new participant"
    assert reconnect["guest_token"] == token_a, "reconnect changed the guest token"
    r = client.get(f"/sessions/{session_id}", headers=host_headers)
    guest_count = sum(1 for p in r.json()["participants"] if p["speaker_name"].startswith("Phone"))
    assert guest_count == 2, f"reconnect duplicated a phone (have {guest_count} guests)"

    assert client.get(f"/sessions/{session_id}/status", headers=headers_a).status_code == 200
    assert client.get(f"/sessions/{session_id}/status").status_code == 401
    assert (
        client.get(
            f"/sessions/{session_id}/status",
            headers={"Authorization": "Bearer not-a-real-token"},
        ).status_code
        == 401
    )

    # Host starts the take; guests then upload using only their guest token.
    r = client.post(f"/sessions/{session_id}/start", headers=host_headers)
    assert r.status_code == 200, r.text
    take_id = r.json()["current_take_id"]

    length = 4.0
    wav_a = make_wav(clap_at_s=0.5, length_s=length, seed=31)
    wav_b = make_wav(clap_at_s=0.9, length_s=length, seed=32)

    # Guest A uploads, then RETRIES the same upload (same token) — both succeed.
    _upload(client, headers_a, session_id, part_a, take_id, wav_a, length)
    _upload(client, headers_a, session_id, part_a, take_id, wav_a, length)
    # Guest B uploads once.
    _upload(client, headers_b, session_id, part_b, take_id, wav_b, length)

    # A guest may NOT upload for another guest's participant.
    r = client.post(
        "/recordings",
        data={
            "session_id": session_id,
            "participant_id": part_b,  # B's participant
            "take_id": take_id,
            "sample_rate": str(SR),
            "duration_seconds": str(length),
        },
        files={"file": ("x.wav", wav_a, "audio/wav")},
        headers=headers_a,  # but using A's token
    )
    assert r.status_code == 403, f"cross-guest upload should be forbidden, got {r.status_code}"

    # Dedup: despite A's retry, selection is exactly one recording per guest.
    db = SessionLocal()
    try:
        selected = _select_input_recordings(db, session_id)
    finally:
        db.close()
    assert len(selected) == 2, (
        f"expected 2 inputs (one per guest), got {len(selected)} — retry was not deduped"
    )

    # Host can see both guests in the participant list (plus the host record).
    r = client.get(f"/sessions/{session_id}", headers=host_headers)
    assert r.status_code == 200, r.text
    names = {p["speaker_name"] for p in r.json()["participants"]}
    assert {"Phone A", "Phone B"}.issubset(names), names

    # Process and confirm the mix is one take's length (no duplicate/stacked audio).
    client.post(f"/sessions/{session_id}/stop", headers=host_headers)
    r = client.post(f"/projects/process/{session_id}", headers=host_headers)
    assert r.status_code == 202, r.text
    project = client.get(f"/projects/{session_id}", headers=host_headers).json()
    assert project["processing_status"] == "done", project
    mixed_dur = _wav_duration_seconds(client.get(project["final_audio_url"]).content)
    assert length - 0.2 <= mixed_dur <= length + 1.0, (
        f"guest mix {mixed_dur:.2f}s outside expected ~{length:.2f}s (duplication?)"
    )

    print("GUEST NO-ACCOUNT FLOW TEST PASSED")
    print("  2 phones joined by code (no login), each got its own token;")
    print("  retry deduped to 1 recording/guest; cross-guest upload blocked.")
    print("  restart/reconnect with same token reused the same participant.")


if __name__ == "__main__":
    main()
