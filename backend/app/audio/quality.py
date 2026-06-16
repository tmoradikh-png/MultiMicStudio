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

from app.audio import processing

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
            tracks = [
                processing.LoadedTrack(c.label, c.mono, c.sr, None)
                for c in raw_clips[:2]
            ]
            offs = processing.compute_offsets(tracks)
            vals = list(offs.values())
            raw_offset_ms = abs(vals[0] - vals[1]) / raw_clips[0].sr * 1000.0
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
                {"question": q, "answer": a, "good": g}
                for (q, a, _d, g) in summary
            ],
        }
    except Exception:  # noqa: BLE001  (badge is best-effort; never break the page)
        logger.exception("Quality evaluation failed")
        return None
