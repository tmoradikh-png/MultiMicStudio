"""Productised audio quality gate (the pass/fail badge for the web dashboard).

This reuses the SAME analysis and baseline checks as the QA test bench
(``scripts/audio_bench.py``) so the badge a host sees matches the bench report —
one source of truth, no divergent re-implementation. It is strictly read-only and
never alters audio; it only measures already-produced files.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger("multimic.quality")

_BENCH_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audio_bench.py"
_bench = None


def _load_bench():
    """Import scripts/audio_bench.py once and cache it.

    The bench is a standalone script (not a package module), so it is loaded by
    file path. ``sys.modules`` must be set BEFORE exec so its @dataclass definitions
    can resolve their own module.
    """
    global _bench
    if _bench is not None:
        return _bench
    spec = importlib.util.spec_from_file_location("audio_bench", _BENCH_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Cannot load audio bench from {_BENCH_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["audio_bench"] = module
    spec.loader.exec_module(module)
    _bench = module
    return module


def evaluate(
    natural_path: str | None,
    raw_paths: list[str],
    studio_path: str | None = None,
) -> dict | None:
    """Run the bench checks against a session's outputs and return a compact badge.

    Returns ``None`` if there is nothing to judge or analysis fails (the dashboard
    then simply shows the players without a badge). The returned dict is:

        {
          "ok": bool,                 # overall pass (no failures, no regressions)
          "passed": int, "total": int, "failed": int,
          "baseline_failed": int, "baseline_total": int,
          "summary": [ {question, answer, good} ],  # plain-language headline Q&A
        }
    """
    try:
        bench = _load_bench()
        clips = []
        for i, p in enumerate(raw_paths[:2]):
            label = "single_phone" if i == 0 else "raw_phone_2"
            clips.append(bench.load_clip_as(label, p))
        if natural_path:
            clips.append(bench.load_clip_as("natural", natural_path))
        if studio_path:
            clips.append(bench.load_clip_as("studio_voice", studio_path))
        if not clips:
            return None

        metrics = {c.label: bench.analyze(c) for c in clips}

        raw_offset_ms = None
        drift_ms = None
        raw_clips = [c for c in clips if c.role == "raw"]
        if len(raw_clips) >= 2:
            # Residual offset AFTER alignment (the raw start gap between phones is
            # corrected automatically; what matters is the leftover sync error).
            raw_offset_ms = bench.residual_offset_ms(
                raw_clips[0].mono, raw_clips[1].mono, raw_clips[0].sr
            )
            drift_ms = bench.estimate_drift_ms(
                raw_clips[0].mono, raw_clips[1].mono, raw_clips[0].sr
            )

        checks = bench.run_checks(metrics, raw_offset_ms, drift_ms)
        baseline = bench.load_baseline()
        baseline_checks = bench.check_baseline(
            metrics, raw_offset_ms, drift_ms, baseline
        )
        summary = bench.build_summary(metrics, checks, baseline_checks)

        func_fail = sum(1 for c in checks if not c.passed)
        base_fail = sum(1 for c in baseline_checks if not c.passed)
        return {
            "ok": func_fail == 0 and base_fail == 0,
            "passed": len(checks) - func_fail,
            "total": len(checks),
            "failed": func_fail,
            "baseline_failed": base_fail,
            "baseline_total": len(baseline_checks),
            "summary": [
                {"question": q, "answer": a, "detail": d, "good": g}
                for (q, a, d, g) in summary
            ],
        }
    except Exception:  # noqa: BLE001  (badge is best-effort; never break the page)
        logger.exception("Quality evaluation failed")
        return None


def report(
    natural_path: str | None,
    raw_paths: list[str],
) -> dict | None:
    """Plain-language quality report for the in-app result screen.

    Maps the same bench measurements to simple words a normal user understands,
    plus a 0-100 score. Read-only; never alters audio. Returns ``None`` if there is
    nothing to measure. Shape:

        {
          "available": True,
          "sync": "Excellent|Good|Problem",
          "stereo_width": "Strong|Medium|Weak",
          "noise": "Low|Medium|High",
          "clipping": "No|Yes",
          "duplicate": "No|Yes",
          "score": 0..100,
        }
    """
    try:
        bench = _load_bench()
        if not natural_path:
            return None
        nat = bench.analyze(bench.load_clip_as("natural", natural_path))

        # Sync — residual offset AFTER alignment between the two raw phones.
        raw_offset_ms = None
        if len(raw_paths) >= 2:
            a = bench.load_clip_as("single_phone", raw_paths[0])
            b = bench.load_clip_as("raw_phone_2", raw_paths[1])
            raw_offset_ms = bench.residual_offset_ms(a.mono, b.mono, a.sr)
        if raw_offset_ms is None:
            sync, sync_pts = ("Good", 18)  # single phone: nothing to be out of sync
        elif raw_offset_ms <= 25:
            sync, sync_pts = ("Excellent", 25)
        elif raw_offset_ms <= 60:
            sync, sync_pts = ("Good", 18)
        else:
            sync, sync_pts = ("Problem", 6)

        # Stereo width — pan movement range of the natural mix.
        pan = float(nat.stereo.get("pan_range", 0.0))
        if pan >= 1.4:
            width, width_pts = ("Strong", 25)
        elif pan >= 0.8:
            width, width_pts = ("Medium", 17)
        else:
            width, width_pts = ("Weak", 8)

        # Noise — signal-to-noise ratio of the natural mix.
        snr = float(nat.snr_db)
        if snr >= 35:
            noise, noise_pts = ("Low", 25)
        elif snr >= 22:
            noise, noise_pts = ("Medium", 16)
        else:
            noise, noise_pts = ("High", 7)

        # Clipping — any samples hitting full scale.
        clipping = "Yes" if nat.clip_count > 0 else "No"
        clip_pts = 0 if nat.clip_count > 0 else 15

        # Duplicate / stacked audio — autocorrelation prominence.
        dup_bad = float(nat.dup_prominence) >= 0.45
        duplicate = "Yes" if dup_bad else "No"
        dup_pts = 0 if dup_bad else 10

        score = int(
            max(0, min(100, sync_pts + width_pts + noise_pts + clip_pts + dup_pts))
        )
        return {
            "available": True,
            "sync": sync,
            "stereo_width": width,
            "noise": noise,
            "clipping": clipping,
            "duplicate": duplicate,
            "score": score,
        }
    except Exception:  # noqa: BLE001 — advisory; never break the response
        logger.exception("Quality report failed")
        return None
