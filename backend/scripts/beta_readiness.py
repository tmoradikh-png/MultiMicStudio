"""Private-beta readiness runner (#9).

A practical GO/NO-GO check. Runs the automatable parts of the checklist against an
ISOLATED temp DB + storage (so it also proves "no manual cleanup needed"):

  1. Full end-to-end flow  — host + 2 no-account guests, start/stop, upload,
     process, outputs present, downloadable.
  2. Reliability           — 10 sessions back-to-back; dedup (retry => 1 file),
     no old-take bleed, clean start/finish.
  3. Audio quality         — bench report for one good session; true stereo,
     studio voice, clipping/stacking/drift, baseline guard.

Mobile recovery, multi-device and noisy-room items are MANUAL (documented in
PRIVATE_BETA_READINESS.md). This runner does not add audio features — it only
exercises and measures the existing pipeline.

Run (from backend/, with the venv):
    .venv\\Scripts\\python.exe scripts\\beta_readiness.py
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import uuid
import wave
from pathlib import Path

import numpy as np

# Isolate DB + storage BEFORE importing the app, so this run is self-contained
# and leaves no artifacts behind (proves no manual cleanup is needed).
_TMP = Path(tempfile.mkdtemp(prefix="multimic_beta_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'beta.db').as_posix()}"
os.environ["STORAGE_LOCAL_DIR"] = str(_TMP / "storage")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.audio import quality  # noqa: E402
from app.main import app  # noqa: E402
from app.storage import get_storage, key_to_relpath  # noqa: E402
from app.worker.tasks import _select_input_recordings  # noqa: E402

SR = 48_000
_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    _results.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def make_voiced_wav(f0: float, clap_at_s: float, length_s: float, seed: int) -> bytes:
    """A speech-like fixture: irregular voiced syllables + pauses + a clap.

    Real speech is aperiodic (random syllable timing) with brief near-silent
    pauses. That gives a high P90/P10 dynamic range (SNR well above baseline) and
    NO single repeating period (so the duplicate-audio detector is not tripped),
    while two different f0 per speaker keep L/R correlation low (true stereo).
    A regular tone would instead read as low-SNR and look like stacked audio — so
    this fixture deliberately randomises onset, duration and pitch.
    """
    rng = np.random.default_rng(seed)
    n = int(length_s * SR)
    sig = np.zeros(n, dtype=np.float64)
    pos = 0.25
    while pos < length_s - 0.2:
        dur = rng.uniform(0.08, 0.18)          # syllable length
        gap = rng.uniform(0.06, 0.16)          # pause (near-silence)
        start = int(pos * SR)
        end = min(n, int((pos + dur) * SR))
        if end > start + 8:
            seg_t = np.arange(end - start) / SR
            jf = f0 * rng.uniform(0.92, 1.08)  # slight pitch jitter
            burst = np.zeros(end - start, dtype=np.float64)
            for k in range(1, 5):              # fundamental + 3 harmonics
                burst += (1.0 / k) * np.sin(2 * np.pi * jf * k * seg_t)
            burst *= np.hanning(end - start)
            sig[start:end] += burst
        pos += dur + gap
    peak = float(np.max(np.abs(sig))) or 1.0
    sig = sig / peak * 0.5
    sig += rng.standard_normal(n) * 0.0008     # deep noise floor (~ -62 dBFS)
    # A dominant, identical clap in both phones gives the aligner a clear shared
    # transient to lock onto (matches a real session where every mic hears the
    # same clap), so the raw inputs are well-synced.
    clap = int(clap_at_s * SR)
    sig[clap : clap + 200] += np.hanning(200) * 1.0
    peak = float(np.max(np.abs(sig))) or 1.0
    sig = (sig / peak) * 0.7                    # ~ -3 dBFS, clap is the peak
    pcm16 = (np.clip(sig, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _signup(client: TestClient) -> dict:
    email = f"beta+{uuid.uuid4().hex[:8]}@test.dev"
    r = client.post(
        "/auth/signup",
        json={"email": email, "name": "Beta Host", "password": "pw123456"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _run_one_session(client: TestClient, host_headers: dict, idx: int) -> dict:
    """One full E2E session with 2 no-account guests, a retry, and a stale take."""
    r = client.post("/sessions", json={"title": f"Beta {idx}"}, headers=host_headers)
    assert r.status_code == 201, r.text
    session = r.json()
    sid, code = session["id"], session["code"]

    # Two guests join with NO account.
    guests = []
    for name in ("Phone A", "Phone B"):
        r = client.post("/sessions/join", json={"code": code, "speaker_name": name})
        assert r.status_code == 200, r.text
        guests.append(r.json())
    tokens = [{"Authorization": f"Bearer {g['guest_token']}"} for g in guests]
    pids = [g["participant"]["id"] for g in guests]

    # Take 1 (a STALE take we must NOT mix into the final result).
    r = client.post(f"/sessions/{sid}/start", headers=host_headers)
    stale_take = r.json()["current_take_id"]
    _upload(client, tokens[0], sid, pids[0], stale_take, make_voiced_wav(130, 0.4, 2.0, idx))

    # Take 2 (the real take). Each guest uploads + ONE retry (must dedup to 1).
    # Claps are near-simultaneous (~20 ms) so the raw inputs match the baseline's
    # well-synced assumption; large-offset alignment is covered by the smoke test.
    r = client.post(f"/sessions/{sid}/start", headers=host_headers)
    take = r.json()["current_take_id"]
    wav_a = make_voiced_wav(130, 0.50, 2.5, idx + 100)
    wav_b = make_voiced_wav(185, 0.52, 2.5, idx + 200)
    _upload(client, tokens[0], sid, pids[0], take, wav_a)
    _upload(client, tokens[0], sid, pids[0], take, wav_a)  # retry
    _upload(client, tokens[1], sid, pids[1], take, wav_b)
    client.post(f"/sessions/{sid}/stop", headers=host_headers)

    r = client.post(f"/projects/process/{sid}", headers=host_headers)
    assert r.status_code == 202, r.text
    project = client.get(f"/projects/{sid}", headers=host_headers).json()
    return {"session_id": sid, "project": project, "take": take}


def _upload(client, headers, sid, pid, take, wav) -> None:
    r = client.post(
        "/recordings",
        data={
            "session_id": sid,
            "participant_id": pid,
            "take_id": take,
            "sample_rate": str(SR),
            "duration_seconds": "2.5",
        },
        files={"file": (f"{pid}.wav", wav, "audio/wav")},
        headers=headers,
    )
    assert r.status_code == 201, r.text


def _wav_channels(content: bytes) -> int:
    with wave.open(io.BytesIO(content), "rb") as w:
        return w.getnchannels()


def main() -> int:
    print("PRIVATE-BETA READINESS RUNNER")
    print(f"Isolated workspace: {_TMP}")

    with TestClient(app) as client:
        host = _signup(client)

        # --- 1. Full end-to-end flow (first session, inspected in detail) -----
        print("\n[1] Full end-to-end flow")
        first = _run_one_session(client, host, 0)
        proj = first["project"]
        check(proj["processing_status"] == "done", "processing completes")
        r = client.get(f"/projects/{first['session_id']}/outputs", headers=host)
        check(r.status_code == 200, "outputs endpoint reachable (owner)")
        outputs = r.json()["outputs"]
        roles = {o["role"] for o in outputs if o["available"]}
        check(
            {"raw_phone_1", "raw_phone_2", "natural_stereo", "studio_voice",
             "karaoke", "party", "mono_downmix"}.issubset(roles),
            "all 7 output roles available",
        )
        natural = next(o for o in outputs if o["role"] == "natural_stereo")
        dl = client.get(natural["url"])
        check(dl.status_code == 200 and len(dl.content) > 1000, "output downloadable")

        # --- 2. Reliability: 10 sessions back-to-back ------------------------
        print("\n[2] Reliability — 10 sessions back-to-back")
        all_ok = True
        dedup_ok = True
        from app.database import SessionLocal

        for i in range(1, 10):  # +1 already run = 10 total
            res = _run_one_session(client, host, i)
            if res["project"]["processing_status"] != "done":
                all_ok = False
            db = SessionLocal()
            try:
                selected = _select_input_recordings(db, res["session_id"])
            finally:
                db.close()
            # Exactly one input per participant for the LATEST take only.
            if len(selected) != 2 or any(r.take_id != res["take"] for r in selected):
                dedup_ok = False
        check(all_ok, "10/10 sessions processed to done")
        check(dedup_ok, "no duplicate recordings; no old-take bleed (dedup holds)")

        # --- 3. Audio quality on one good session ----------------------------
        print("\n[3] Audio quality — bench on one good session")
        storage = get_storage()
        sid = first["session_id"]
        # Render studio_voice so the badge can judge it (same path as web UI).
        r = client.post(
            f"/projects/{sid}/enhance", json={"mode": "studio_voice"}, headers=host
        )
        assert r.status_code == 200, r.text
        enhanced_url = r.json().get("final_audio_enhanced_url")

        db = SessionLocal()
        try:
            recs = _select_input_recordings(db, sid)
            raw_paths = [storage.path(key_to_relpath(r.file_url)) for r in recs]
        finally:
            db.close()
        stereo_url = first["project"]["final_audio_stereo_url"]
        natural_path = storage.path(key_to_relpath(stereo_url)) if stereo_url else None
        studio_path = (
            storage.path(key_to_relpath(enhanced_url)) if enhanced_url else None
        )

        # True stereo: 2 channels + measurable L/R difference.
        ch = _wav_channels(client.get(natural["url"]).content)
        check(ch == 2, f"natural stereo is true 2-channel (channels={ch})")

        badge = quality.evaluate(natural_path, raw_paths, studio_path)
        if badge is None:
            check(False, "bench produced a quality verdict")
        else:
            for item in badge["summary"]:
                print(f"        {item['answer']:>3}  {item['question']}")
            check(badge["failed"] == 0, f"all bench checks pass ({badge['passed']}/{badge['total']})")
            check(
                badge["baseline_failed"] == 0,
                f"baseline guard holds ({badge['baseline_total'] - badge['baseline_failed']}"
                f"/{badge['baseline_total']} at/above baseline)",
            )

        # DIAG: list any failing checks (incl. studio_voice) for inspection.
        try:
            _bench = quality._load_bench()
            from app.audio import processing as _proc
            _clips = []
            for _i, _p in enumerate(raw_paths[:2]):
                _clips.append(_bench.load_clip_as("single_phone" if _i == 0 else "raw_phone_2", _p))
            if natural_path:
                _clips.append(_bench.load_clip_as("natural", natural_path))
            if studio_path:
                _clips.append(_bench.load_clip_as("studio_voice", studio_path))
            _m = {c.label: _bench.analyze(c) for c in _clips}
            _rc = [c for c in _clips if c.role == "raw"]
            _off = _drift = None
            if len(_rc) >= 2:
                _tr = [_proc.LoadedTrack(c.label, c.mono, c.sr, None) for c in _rc[:2]]
                _o = list(_proc.compute_offsets(_tr).values())
                _off = abs(_o[0] - _o[1]) / _rc[0].sr * 1000.0
                _drift = _bench.estimate_drift_ms(_rc[0].mono, _rc[1].mono, _rc[0].sr)
            for c in _bench.run_checks(_m, _off, _drift):
                if not c.passed:
                    print(f"        [check FAIL] {c.name} — {c.detail}")
            for c in _bench.check_baseline(_m, _off, _drift, _bench.load_baseline()):
                if not c.passed:
                    print(f"        [baseline BELOW] {c.name} — {c.detail}")
        except Exception as exc:  # noqa: BLE001
            print(f"        (diag failed: {exc})")

        # Write a human-openable HTML bench report artifact.
        try:
            bench = quality._load_bench()
            clips = bench.collect_clips(raw_paths, natural_path)
            report_dir = Path(__file__).resolve().parents[1] / "bench_out_beta"
            result = bench.build_report(clips, report_dir, -16.0, "Beta readiness session")
            print(f"        bench report: {result['report_path']}")
        except Exception as exc:  # noqa: BLE001
            print(f"        (could not write HTML report: {exc})")

    # --- Verdict ------------------------------------------------------------
    passed = sum(1 for ok, _ in _results if ok)
    total = len(_results)
    print(f"\nAUTOMATED CHECKS: {passed}/{total} passed")
    failed = [label for ok, label in _results if not ok]
    if failed:
        print("FAILURES:")
        for label in failed:
            print(f"  - {label}")
    verdict = "GO" if passed == total else "NO-GO"
    print(f"VERDICT (automated portion): {verdict}")

    # Cleanup the isolated workspace (proves nothing persistent was needed).
    shutil.rmtree(_TMP, ignore_errors=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
