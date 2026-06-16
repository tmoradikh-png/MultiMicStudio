"""Audio synchronization and mixing engine.

This is the core of the MVP milestone: take several independently-recorded files and
produce one aligned, mixed track. The strategy is layered (see docs/ROADMAP.md):

  Layer 1  Sync marker (clap/beep) peak detection.
  Layer 2  Waveform cross-correlation to refine the offset.
  Layer 3  Drift correction for long takes  -> hook present, returns 0 in MVP.

All decoding/encoding goes through FFmpeg so any phone format (m4a/aac/wav) works.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import uniform_filter1d

TARGET_SAMPLE_RATE = 48_000  # spec: 48 kHz preferred

logger = logging.getLogger("multimic.processing")


class AudioEngineError(RuntimeError):
    pass


def _ffmpeg_bin() -> str:
    # Prefer a system FFmpeg; otherwise fall back to the pip-bundled binary
    # (imageio-ffmpeg), so the engine works without a manual install.
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise AudioEngineError(
        "FFmpeg not found. Install FFmpeg on PATH, or `pip install imageio-ffmpeg`."
    )


def decode_to_wav(src_path: str, sample_rate: int = TARGET_SAMPLE_RATE) -> str:
    """Decode any input to mono float WAV at a fixed sample rate. Returns temp path."""
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    cmd = [
        _ffmpeg_bin(), "-y", "-i", src_path,
        "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", out.name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AudioEngineError(f"FFmpeg decode failed: {proc.stderr[-500:]}")
    return out.name


@dataclass
class LoadedTrack:
    recording_id: str
    samples: np.ndarray          # mono float32, normalized -1..1
    sample_rate: int
    local_start_ms: float | None  # client-reported start, for coarse alignment


def load_track(
    recording_id: str, src_path: str, local_start_ms: float | None
) -> LoadedTrack:
    wav_path = decode_to_wav(src_path)
    try:
        data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
    finally:
        Path(wav_path).unlink(missing_ok=True)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return LoadedTrack(recording_id, data, sr, local_start_ms)


# --- Layer 1: sync marker (clap/beep) ---------------------------------------

def detect_marker_offset(
    samples: np.ndarray, sample_rate: int, search_seconds: float = 10.0
) -> int | None:
    """Find the sample index of the FIRST sharp transient (clap/beep) near the start.

    We want the ONSET of the marker, not the loudest sample overall — a later loud
    voice must not be mistaken for the sync marker, and the onset is what we align.

    Approach:
      * compute a short-term energy envelope,
      * derive a robust baseline from the quietest part of the lead-in,
      * return the first sample whose energy clearly exceeds that baseline.
    Returns the onset sample index, or None if no clear transient is present.
    """
    window = max(1, int(0.005 * sample_rate))  # 5 ms
    search = samples[: int(search_seconds * sample_rate)]
    if search.size < window:
        return None

    energy = np.convolve(search**2, np.ones(window), mode="same")
    peak = float(np.max(energy))
    if peak <= 0:
        return None

    # Robust noise floor: 20th percentile of the envelope (ignores the transient).
    baseline = float(np.percentile(energy, 20)) + 1e-12
    # Require the peak to stand well above the floor to count as a real marker.
    if peak < baseline * 12:
        return None

    # Onset = first crossing of a threshold set between floor and peak.
    threshold = max(baseline * 8, peak * 0.30)
    above = np.flatnonzero(energy >= threshold)
    if above.size == 0:
        return None
    # Step back to the start of the energy window so we point at the true onset.
    return int(max(0, above[0] - window // 2))


# --- Layer 2: cross-correlation refinement ----------------------------------

def cross_correlation_offset(
    reference: np.ndarray, other: np.ndarray, sample_rate: int, max_lag_s: float = 5.0
) -> int:
    """Return the offset (in samples) to place `other` so it aligns with `reference`.

    The returned value O satisfies: an event at position p in `reference` appears at
    position p - O in `other`. Placing `other` at offset O therefore aligns them.
    """
    lag, _ = cross_correlation_offset_conf(reference, other, sample_rate, max_lag_s)
    return lag


def cross_correlation_offset_conf(
    reference: np.ndarray,
    other: np.ndarray,
    sample_rate: int,
    max_lag_s: float = 5.0,
) -> tuple[int, float]:
    """Like `cross_correlation_offset` but also returns a 0..1 confidence.

    Confidence is the normalized correlation peak (peak / sqrt(energy_a*energy_b)),
    so a sharp, unambiguous alignment scores near 1 and noise scores near 0.
    """
    max_lag = int(max_lag_s * sample_rate)
    n = min(len(reference), len(other), 30 * sample_rate)  # cap to 30 s for speed
    if n <= 1:
        return 0, 0.0
    a = reference[:n] - np.mean(reference[:n])
    b = other[:n] - np.mean(other[:n])
    corr = signal.correlate(a, b, mode="full", method="fft")
    lags = signal.correlation_lags(len(a), len(b), mode="full")
    mask = np.abs(lags) <= max_lag
    corr, lags = corr[mask], lags[mask]
    if corr.size == 0:
        return 0, 0.0
    idx = int(np.argmax(np.abs(corr)))
    norm = float(np.sqrt(np.sum(a**2) * np.sum(b**2))) + 1e-12
    conf = float(abs(corr[idx]) / norm)
    return int(lags[idx]), conf


def _energy_envelope(samples: np.ndarray, sample_rate: int, env_rate: int = 200):
    """Down-sample to a loudness envelope (~`env_rate` Hz) of the signal.

    Both phones in a room hear the same overall loudness pattern of the conversation
    even when each sits closer to a different speaker, so correlating envelopes is far
    more robust to mic placement/level differences than correlating raw waveforms.
    """
    step = max(1, int(sample_rate / env_rate))
    win = step
    energy = np.convolve(samples.astype(np.float32) ** 2, np.ones(win), mode="same")
    env = energy[::step]
    # Compress dynamic range so loud transients don't dominate the correlation.
    env = np.sqrt(np.maximum(env, 0.0))
    return env, sample_rate / step


def envelope_correlation_offset_conf(
    reference: np.ndarray,
    other: np.ndarray,
    sample_rate: int,
    max_lag_s: float = 5.0,
) -> tuple[int, float]:
    """Robust coarse offset (in original samples) via loudness-envelope correlation."""
    ref_env, env_rate = _energy_envelope(reference, sample_rate)
    oth_env, _ = _energy_envelope(other, sample_rate)
    lag_env, conf = cross_correlation_offset_conf(
        ref_env, oth_env, int(env_rate), max_lag_s
    )
    # Scale the envelope-domain lag back to original sample rate.
    return int(round(lag_env * sample_rate / env_rate)), conf


def refine_offset(
    reference: np.ndarray,
    other: np.ndarray,
    sample_rate: int,
    coarse_offset: int,
    window_ms: float = 120.0,
) -> int:
    """Lock a coarse offset to sample accuracy with a local raw cross-correlation.

    Searches only ±`window_ms` around `coarse_offset`, so it refines without the
    risk of jumping to a spurious global peak elsewhere in the signal.
    """
    win = int(window_ms / 1000 * sample_rate)
    n = min(len(reference), len(other), 30 * sample_rate)
    if n <= 1 or win <= 0:
        return coarse_offset
    a = reference[:n] - np.mean(reference[:n])
    b = other[:n] - np.mean(other[:n])
    best_lag, best_val = coarse_offset, -np.inf
    for lag in range(coarse_offset - win, coarse_offset + win + 1):
        # Overlap a (at position p) with b (at position p - lag).
        if lag >= 0:
            av, bv = a[lag:], b[: len(a) - lag]
        else:
            av, bv = a[: len(b) + lag], b[-lag:]
        m = min(len(av), len(bv))
        if m <= 0:
            continue
        val = float(np.dot(av[:m], bv[:m]))
        if val > best_val:
            best_val, best_lag = val, lag
    return best_lag


# --- Layer 3: drift correction (hook) ---------------------------------------

def detect_drift(reference: LoadedTrack, other: LoadedTrack) -> float:
    """Return estimated clock drift as a sample-rate ratio correction.

    MVP returns 0.0 (no correction). The full product compares marker offsets at the
    start vs. end of long takes and resamples `other` by the measured ratio.
    """
    return 0.0


# --- Alignment + mixing ------------------------------------------------------

# Two offsets are considered "in agreement" if within this many milliseconds.
_AGREE_MS = 120.0


def compute_offsets(tracks: list[LoadedTrack]) -> dict[str, int]:
    """Compute a per-track start offset (in samples) relative to the earliest track.

    Real phones make a single sync marker unreliable: acoustic echo cancellation can
    erase the host's own beep, and a far phone may instead lock onto a later loud
    word. So we no longer trust the marker blindly. For each track we compute up to
    three independent estimates and pick a consensus:

      * marker:    clap/beep onset difference (precise *when* clean),
      * envelope:  loudness-envelope correlation (robust to mic position/level),
      * raw xcorr: full-waveform correlation (precise for shared transients).

    Decision: take the envelope offset as the robust anchor, accept the marker only
    when it agrees with that anchor (then refine to sample accuracy), otherwise fall
    back to the envelope/raw estimate. This prevents a false marker from injecting a
    large delay while keeping sample-accurate sync when the beep really was captured.
    """
    if not tracks:
        return {}
    if len(tracks) == 1:
        # Single phone: no alignment needed; it sits at time zero.
        return {tracks[0].recording_id: 0}

    sr = tracks[0].sample_rate
    agree = int(_AGREE_MS / 1000 * sr)
    markers = {t.recording_id: detect_marker_offset(t.samples, sr) for t in tracks}

    reference = tracks[0]
    ref_marker = markers[reference.recording_id]
    offsets: dict[str, int] = {reference.recording_id: 0}

    for t in tracks[1:]:
        t_marker = markers[t.recording_id]
        marker_off = (
            int(ref_marker - t_marker)
            if (ref_marker is not None and t_marker is not None)
            else None
        )
        env_off, env_conf = envelope_correlation_offset_conf(
            reference.samples, t.samples, sr
        )
        raw_off, raw_conf = cross_correlation_offset_conf(
            reference.samples, t.samples, sr
        )

        # Choose a robust anchor: prefer raw xcorr when it is confident and agrees
        # with the envelope estimate, else trust the envelope (position-robust).
        if raw_conf >= 0.20 and abs(raw_off - env_off) <= agree:
            anchor, method = raw_off, "xcorr"
        else:
            anchor, method = env_off, "envelope"

        if marker_off is not None and abs(marker_off - anchor) <= agree:
            # Beep was genuinely captured by both: use it and lock to sample level.
            chosen = refine_offset(reference.samples, t.samples, sr, marker_off)
            method = "marker"
        else:
            # Refine the robust anchor too, so the final offset is sample-accurate.
            chosen = refine_offset(reference.samples, t.samples, sr, anchor)

        if (
            env_conf < 0.05
            and raw_conf < 0.05
            and reference.local_start_ms is not None
            and t.local_start_ms is not None
        ):
            # Nothing correlated (phones didn't share audio): last-resort timestamp.
            chosen = int((t.local_start_ms - reference.local_start_ms) / 1000 * sr)
            method = "timestamp"

        logger.info(
            "align track=%s method=%s chosen=%.1fms marker=%s env=%.1fms(%.2f) "
            "raw=%.1fms(%.2f)",
            t.recording_id,
            method,
            chosen / sr * 1000,
            f"{marker_off / sr * 1000:.1f}ms" if marker_off is not None else "none",
            env_off / sr * 1000,
            env_conf,
            raw_off / sr * 1000,
            raw_conf,
        )
        offsets[t.recording_id] = chosen

    # Shift so the earliest track starts at 0 (no negative indices).
    min_off = min(offsets.values())
    return {rid: off - min_off for rid, off in offsets.items()}


def mix_tracks(tracks: list[LoadedTrack], offsets: dict[str, int]) -> tuple[np.ndarray, int]:
    """Place each track ONCE at its offset on a common timeline and sum to mono.

    Applies gain control + a soft limiter so the summed signal never clips and a
    single-track mix is returned essentially unchanged.
    """
    sr = tracks[0].sample_rate
    total_len = max(offsets[t.recording_id] + len(t.samples) for t in tracks)
    mix = np.zeros(total_len, dtype=np.float32)
    for t in tracks:
        start = offsets[t.recording_id]
        mix[start : start + len(t.samples)] += t.samples

    if len(tracks) > 1:
        # Reduce gain ahead of summation headroom, then peak-normalize if needed.
        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 1.0:
            mix /= peak
            # Soft limiter to tame any residual transients post-normalization.
            mix = np.tanh(mix).astype(np.float32)
    return mix, sr


def _default_pans(n: int) -> list[float]:
    """Stereo pan position per track in [-1 (left) .. +1 (right)].

    One track is centered; two are placed clearly left/right (Phone A left, Phone B
    right); more are spread evenly. This is intentional placement for audible width,
    not a claim of true stereo capture.
    """
    if n <= 1:
        return [0.0]
    if n == 2:
        return [-0.7, 0.7]
    return list(np.linspace(-0.8, 0.8, n))


# --- Natural-mix quality cleanup --------------------------------------------
#
# Goal (per reviewer feedback): the natural stereo mix must not only be more
# spatial than a single phone, but also *cleaner* and *better balanced* — without
# destroying real left/right movement. The steps below run BEFORE any enhancement
# mode, so the natural mix is the clean reference the effects build on.

def _rms_envelope(x: np.ndarray, sr: int, window_ms: float = 20.0) -> np.ndarray:
    """Short-term RMS envelope (same length as ``x``)."""
    win = max(1, int(window_ms / 1000.0 * sr))
    return np.sqrt(uniform_filter1d(x.astype(np.float64) ** 2, win) + 1e-12)


def _noise_rms(x: np.ndarray, sr: int) -> float:
    """Estimate a per-track noise floor as a low percentile of the RMS envelope."""
    env = _rms_envelope(x, sr)
    return float(np.percentile(env, 10))


def _active_rms(x: np.ndarray, sr: int) -> float:
    """Loudness of the *active* (speech/signal) part, ignoring silent gaps."""
    env = _rms_envelope(x, sr)
    hi = float(np.percentile(env, 75))
    active = env[env >= hi]
    if active.size:
        return float(np.sqrt(np.mean(active ** 2)))
    return float(np.sqrt(np.mean(env ** 2)))


def _noise_gate(x: np.ndarray, sr: int, noise_rms: float) -> np.ndarray:
    """Light downward expander: attenuate sections near the noise floor.

    Real signal (well above the floor) passes at unity; quiet gaps are pulled down
    smoothly so the mixed output picks up less hiss/room tone from each input. The
    gain is floored (never fully muted) and smoothed to avoid pumping/zipper noise.
    Length is preserved exactly.
    """
    if noise_rms <= 0 or x.size == 0:
        return x.astype(np.float32, copy=False)
    env = _rms_envelope(x, sr)
    thresh = noise_rms * 3.0  # ~+9.5 dB above the floor: open for genuine signal
    ratio = 2.0
    gain = np.ones_like(env)
    below = env < thresh
    gain[below] = np.clip((env[below] / (thresh + 1e-12)) ** (ratio - 1.0), 0.15, 1.0)
    # Smooth the gain curve so attenuation fades in/out gently.
    gain = uniform_filter1d(gain, max(1, int(0.03 * sr)))
    return (x * gain).astype(np.float32)


def _balance_stereo(
    stereo: np.ndarray, max_db: float = 3.0, correction: float = 0.7
) -> np.ndarray:
    """Gentle overall L/R balance correction.

    Corrects only the *overall* channel-level imbalance (e.g. one phone louder than
    the other), not the moment-to-moment panning — so real left/right movement is
    preserved. The correction is partial (``correction``) and capped (``max_db``) so
    audio intentionally placed hard to one side is not flattened to centre. Equal,
    opposite gains keep total energy roughly constant.
    """
    if stereo.size == 0:
        return stereo
    rms_l = float(np.sqrt(np.mean(stereo[:, 0] ** 2)))
    rms_r = float(np.sqrt(np.mean(stereo[:, 1] ** 2)))
    if rms_l <= 1e-9 or rms_r <= 1e-9:
        return stereo
    diff_db = 20.0 * np.log10(rms_l / rms_r)  # +ve => left louder
    corr_db = float(np.clip(diff_db * correction, -max_db, max_db))
    gain_l = 10.0 ** (-corr_db / 2.0 / 20.0)
    gain_r = 10.0 ** (corr_db / 2.0 / 20.0)
    out = stereo.copy()
    out[:, 0] *= gain_l
    out[:, 1] *= gain_r
    return out.astype(np.float32)


def _normalize_peak(stereo: np.ndarray, target_dbfs: float = -1.5) -> np.ndarray:
    """Peak-normalize to a safe target so there is real headroom before export.

    Replaces the old tanh soft-limit (which coloured the sound and pushed peaks to
    ~0 dBFS). A clean linear scale to ``target_dbfs`` leaves headroom and never
    clips.
    """
    peak = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    if peak <= 0:
        return stereo.astype(np.float32, copy=False)
    target = 10.0 ** (target_dbfs / 20.0)
    return (stereo * (target / peak)).astype(np.float32)


def mix_stereo(
    tracks: list[LoadedTrack],
    offsets: dict[str, int],
    pans: list[float] | None = None,
    target_peak_dbfs: float = -1.5,
) -> tuple[np.ndarray, int]:
    """Place each track once at its offset and pan it into a clean stereo field.

    Returns an (N, 2) float32 array. Pipeline (reviewer-requested quality pass):
      1. Light noise gate per input, weighting cleaner mics (higher SNR) higher.
      2. Equal-loudness leveling so symmetric pans give a balanced image while real
         movement inside each track is kept.
      3. Constant-power panning for even perceived loudness across the field.
      4. Gentle overall L/R balance correction (preserves panning movement).
      5. Peak-normalize to a safe headroom target (no clipping, no tanh colour).
    Sample count is never changed, so enhancement modes stay length-matched.
    """
    sr = tracks[0].sample_rate
    pans = pans or _default_pans(len(tracks))
    total_len = max(offsets[t.recording_id] + len(t.samples) for t in tracks)
    stereo = np.zeros((total_len, 2), dtype=np.float32)

    # 1. Clean each input and measure loudness + SNR.
    cleaned: list[np.ndarray] = []
    active: list[float] = []
    snr: list[float] = []
    for t in tracks:
        nr = _noise_rms(t.samples, sr)
        c = _noise_gate(t.samples, sr, nr)
        ar = _active_rms(c, sr)
        cleaned.append(c)
        active.append(ar)
        snr.append(ar / (nr + 1e-9))

    ref = float(np.median([a for a in active if a > 0])) if any(a > 0 for a in active) else 1.0
    max_snr = max(snr) if snr else 1.0

    # 2 + 3. Level-match, weight cleaner mics slightly higher, then pan.
    for c, ar, s, t, pan in zip(cleaned, active, snr, tracks, pans):
        norm = float(np.clip(ref / ar, 0.25, 4.0)) if ar > 1e-9 else 1.0
        # Cleaner mics get a mild lift (max ~+0.2x); keeps imbalance from returning.
        weight = (s / max_snr) if max_snr > 0 else 1.0
        gain = norm * (0.8 + 0.2 * weight)
        start = offsets[t.recording_id]
        angle = (float(np.clip(pan, -1.0, 1.0)) + 1.0) / 4.0 * np.pi  # 0..pi/2
        gain_l, gain_r = float(np.cos(angle)), float(np.sin(angle))
        seg = c * gain
        stereo[start : start + len(seg), 0] += seg * gain_l
        stereo[start : start + len(seg), 1] += seg * gain_r

    # 4. Gentle overall balance, then 5. safe headroom.
    stereo = _balance_stereo(stereo)
    stereo = _normalize_peak(stereo, target_peak_dbfs)
    return stereo, sr


def build_stem(track: LoadedTrack, offset: int, total_len: int) -> np.ndarray:
    """Single participant's audio placed on the shared timeline (normalized mono)."""
    stem = np.zeros(total_len, dtype=np.float32)
    stem[offset : offset + len(track.samples)] += track.samples
    peak = float(np.max(np.abs(stem))) if stem.size else 0.0
    if peak > 0:
        stem = (stem / peak * 0.9).astype(np.float32)
    return stem



def write_wav(samples: np.ndarray, sample_rate: int, dest_path: str) -> None:
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(dest_path, samples, sample_rate, subtype="PCM_16")


@dataclass
class MixDiagnostics:
    """Per-run mixing report (logged by the worker)."""

    input_count: int
    sample_rate: int
    final_duration_seconds: float
    alignment_method: str  # "single" | "marker" | "correlation" | "timestamp"
    markers_missing: list[str]  # recording_ids with no detectable sync marker
    # recording_id -> info dict:
    #   duration_seconds, offset_ms, marker_found, marker_ms
    per_track: dict[str, dict[str, float | bool]]
    # Local output paths produced alongside the mono mix (worker uploads them).
    stereo_path: str | None = None
    stem_paths: dict[str, str] = field(default_factory=dict)  # recording_id -> path


def align_and_mix(
    inputs: list[tuple[str, str, float | None]],
    dest_wav_path: str,
    stereo_dest_path: str | None = None,
    stems_dir: str | None = None,
) -> MixDiagnostics:
    """High-level entry point.

    inputs: list of (recording_id, source_file_path, local_start_ms).
    Writes the mono mixed WAV to dest_wav_path. When `stereo_dest_path` / `stems_dir`
    are given, also writes a stereo render and per-participant stems, returning their
    local paths in the diagnostics for the worker to persist.
    """
    tracks = [load_track(rid, path, ts) for rid, path, ts in inputs]
    if not tracks:
        raise AudioEngineError("No tracks to mix")

    sr = tracks[0].sample_rate

    if len(tracks) == 1:
        # Single phone: write the raw track through unchanged (no overlay/repeat).
        t = tracks[0]
        write_wav(t.samples, sr, dest_wav_path)
        marker = detect_marker_offset(t.samples, sr)
        stem_paths: dict[str, str] = {}
        if stems_dir is not None:
            stem_path = str(Path(stems_dir) / f"stem_{t.recording_id}.wav")
            write_wav(t.samples, sr, stem_path)
            stem_paths[t.recording_id] = stem_path
        return MixDiagnostics(
            input_count=1,
            sample_rate=sr,
            final_duration_seconds=len(t.samples) / sr if sr else 0.0,
            alignment_method="single",
            markers_missing=[],
            per_track={
                t.recording_id: {
                    "duration_seconds": len(t.samples) / sr if sr else 0.0,
                    "offset_ms": 0.0,
                    "marker_found": marker is not None,
                    "marker_ms": (marker / sr * 1000) if (marker is not None and sr) else -1.0,
                }
            },
            stereo_path=None,
            stem_paths=stem_paths,
        )

    markers = {t.recording_id: detect_marker_offset(t.samples, sr) for t in tracks}
    markers_missing = [rid for rid, m in markers.items() if m is None]
    # Method: markers used only when every track has one; else correlation/timestamp.
    alignment_method = "marker" if not markers_missing else "correlation"

    offsets = compute_offsets(tracks)
    mix, sr = mix_tracks(tracks, offsets)
    write_wav(mix, sr, dest_wav_path)
    total_len = len(mix)

    stereo_out: str | None = None
    if stereo_dest_path is not None:
        stereo, _ = mix_stereo(tracks, offsets)
        write_wav(stereo, sr, stereo_dest_path)
        stereo_out = stereo_dest_path

    stem_paths = {}
    if stems_dir is not None:
        for t in tracks:
            stem = build_stem(t, offsets[t.recording_id], total_len)
            stem_path = str(Path(stems_dir) / f"stem_{t.recording_id}.wav")
            write_wav(stem, sr, stem_path)
            stem_paths[t.recording_id] = stem_path

    per_track: dict[str, dict[str, float | bool]] = {}
    for t in tracks:
        m = markers[t.recording_id]
        per_track[t.recording_id] = {
            "duration_seconds": len(t.samples) / sr if sr else 0.0,
            "offset_ms": offsets[t.recording_id] / sr * 1000 if sr else 0.0,
            "marker_found": m is not None,
            "marker_ms": (m / sr * 1000) if (m is not None and sr) else -1.0,
        }
    return MixDiagnostics(
        input_count=len(tracks),
        sample_rate=sr,
        final_duration_seconds=len(mix) / sr if sr else 0.0,
        alignment_method=alignment_method,
        markers_missing=markers_missing,
        per_track=per_track,
        stereo_path=stereo_out,
        stem_paths=stem_paths,
    )
