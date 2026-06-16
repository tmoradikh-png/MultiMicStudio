"""Audio test bench — objectively compare recordings and mixes.

Answers the product question: *is the multi-mic mix actually better than a single
phone, and is the stereo/spatial information preserved?*  It compares:

  1. a single raw recording (one phone / one channel)
  2. the natural stereo mix
  3. the enhanced mixes (studio_voice, karaoke, party)
  4. (optional) a mono down-mix of the final stereo file

and produces an HTML report with metrics, plots and pass/fail warnings, plus
loudness-normalised A/B listening files so the louder file does not just sound
"better".

Usage (from backend/):

    # Resolve everything from the database for a processed session:
    python scripts/audio_bench.py --session-id <SESSION_ID>

    # Or point it at files directly (no database needed):
    python scripts/audio_bench.py \
        --raw take_phoneA.m4a --raw take_phoneB.m4a \
        --natural final_mix_stereo.wav \
        --out bench_out

Enhanced mixes are generated on the fly from the natural stereo mix using the
exact same preset code the app uses (app.audio.effects), so the bench always has
studio_voice/karaoke/party to analyse and they are guaranteed length-identical.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import html
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Make `app...` importable when run as `python scripts/audio_bench.py` from backend/.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import matplotlib

matplotlib.use("Agg")  # headless rendering
import matplotlib.pyplot as plt  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import signal  # noqa: E402

from app.audio import effects, processing  # noqa: E402

try:
    import pyloudnorm as pyln  # type: ignore

    _HAVE_PYLN = True
except Exception:  # noqa: BLE001
    _HAVE_PYLN = False


# --------------------------------------------------------------------------- #
# Audio container
# --------------------------------------------------------------------------- #
@dataclass
class Clip:
    """A loaded audio clip kept in both stereo (N,2) and mono (N,) form."""

    label: str
    stereo: np.ndarray  # (N, 2) float32
    sr: int
    role: str = "other"  # raw | natural | enhanced | mono_downmix
    source: str | None = None

    @property
    def mono(self) -> np.ndarray:
        return self.stereo.mean(axis=1).astype(np.float32)

    @property
    def n_samples(self) -> int:
        return int(self.stereo.shape[0])

    @property
    def duration(self) -> float:
        return self.n_samples / self.sr


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _ffmpeg_decode_stereo(path: str) -> str:
    """Decode a compressed file to WAV, preserving up to 2 channels (not mono)."""
    import subprocess
    import tempfile

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    cmd = [
        processing._ffmpeg_bin(), "-y", "-i", path,
        "-ar", str(processing.TARGET_SAMPLE_RATE),
        "-c:a", "pcm_s16le", out.name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise processing.AudioEngineError(f"FFmpeg decode failed: {proc.stderr[-400:]}")
    return out.name


def _read_audio(path: str) -> tuple[np.ndarray, int]:
    """Decode any supported file to (N,2) float32 at the project sample rate.

    WAV/FLAC/OGG are read directly so the stereo image is preserved; only truly
    compressed formats (m4a/webm) go through FFmpeg, and even then we keep channels.
    """
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
    except Exception:  # noqa: BLE001  (compressed/container formats)
        wav_path = _ffmpeg_decode_stereo(path)
        try:
            data, sr = sf.read(wav_path, dtype="float32", always_2d=True)
        finally:
            Path(wav_path).unlink(missing_ok=True)

    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]

    target = processing.TARGET_SAMPLE_RATE
    if sr != target:
        g = np.gcd(int(sr), target)
        data = signal.resample_poly(data, target // g, sr // g, axis=0)
        sr = target
    return data.astype(np.float32), sr


def load_clip(label: str, path: str, role: str) -> Clip:
    data, sr = _read_audio(path)
    return Clip(label=label, stereo=data, sr=sr, role=role, source=path)


# Roles the GUI/CLI can attach a file to directly, with the role used for checks.
ROLE_LABELS: dict[str, str] = {
    "single_phone": "raw",
    "raw_phone_2": "raw",
    "natural": "natural",
    "studio_voice": "enhanced",
    "karaoke": "enhanced",
    "party": "enhanced",
    "mono_downmix": "mono_downmix",
}


def load_clip_as(label: str, path: str) -> Clip:
    """Load a file and tag it with the role implied by `label` (for the GUI).

    A mono-down-mix upload is collapsed to one channel so its metrics reflect a
    true mono file even if the source happens to be stereo.
    """
    role = ROLE_LABELS.get(label, "other")
    clip = load_clip(label, path, role)
    if label == "mono_downmix":
        mono = clip.mono
        clip.stereo = np.column_stack([mono, mono]).astype(np.float32)
    return clip


# --------------------------------------------------------------------------- #
# Metric helpers
# --------------------------------------------------------------------------- #
def _dbfs(x: float) -> float:
    return 20.0 * np.log10(max(float(x), 1e-12))


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def lufs(stereo: np.ndarray, sr: int) -> float | None:
    """Integrated loudness (LUFS, ITU-R BS.1770) if pyloudnorm is available."""
    if not _HAVE_PYLN or stereo.shape[0] < int(0.4 * sr):
        return None
    try:
        meter = pyln.Meter(sr)
        val = float(meter.integrated_loudness(stereo))
        return val if np.isfinite(val) else None
    except Exception:  # noqa: BLE001
        return None


def band_energy(mono: np.ndarray, sr: int) -> dict[str, float]:
    """Relative bass/mid/high energy (fractions of total spectral energy)."""
    n = len(mono)
    if n < 16:
        return {"bass": 0.0, "mid": 0.0, "high": 0.0}
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(mono * win)) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    total = float(spec.sum()) or 1.0
    bass = float(spec[freqs < 250].sum()) / total
    mid = float(spec[(freqs >= 250) & (freqs < 4000)].sum()) / total
    high = float(spec[freqs >= 4000].sum()) / total
    return {"bass": bass, "mid": mid, "high": high}


def noise_floor_and_snr(mono: np.ndarray, sr: int) -> tuple[float, float]:
    """Estimate noise floor (dBFS) from the quietest frames and an SNR estimate."""
    frame = max(int(0.05 * sr), 1)
    n_frames = len(mono) // frame
    if n_frames < 4:
        return (-120.0, 0.0)
    rms_frames = np.array(
        [_rms(mono[i * frame : (i + 1) * frame]) for i in range(n_frames)]
    )
    rms_frames = rms_frames[rms_frames > 0]
    if rms_frames.size < 4:
        return (-120.0, 0.0)
    noise = float(np.percentile(rms_frames, 10))
    sig = float(np.percentile(rms_frames, 90))
    snr = _dbfs(sig) - _dbfs(noise)
    return (_dbfs(noise), snr)


def stereo_metrics(stereo: np.ndarray) -> dict:
    """L/R balance, correlation, mono compatibility and movement detection."""
    left, right = stereo[:, 0], stereo[:, 1]
    rms_l, rms_r = _rms(left), _rms(right)
    balance_db = _dbfs(rms_r) - _dbfs(rms_l)  # +ve => louder on the right

    a = left - left.mean()
    b = right - right.mean()
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    corr = float(np.dot(a, b) / denom) if denom > 0 else 1.0

    mid = 0.5 * (left + right)
    side = 0.5 * (left - right)
    side_energy = _rms(side)
    mid_energy = _rms(mid)
    # 0 => pure mono (no width); higher => wider stereo image.
    width = side_energy / (mid_energy + 1e-9)

    # Movement: per-window pan index ( -1 hard left .. +1 hard right ).
    sr_win = max(len(left) // 200, 1)
    pans = []
    for i in range(0, len(left) - sr_win, sr_win):
        l = _rms(left[i : i + sr_win])
        r = _rms(right[i : i + sr_win])
        if (l + r) > 1e-5:
            pans.append((r - l) / (r + l))
    pans = np.array(pans) if pans else np.array([0.0])
    pan_range = float(pans.max() - pans.min())

    return {
        "rms_l_db": _dbfs(rms_l),
        "rms_r_db": _dbfs(rms_r),
        "balance_db": balance_db,
        "correlation": corr,
        "width": width,
        "pan_range": pan_range,
        "pan_series": pans,
    }


def estimate_drift_ms(a: np.ndarray, b: np.ndarray, sr: int) -> float | None:
    """Estimate alignment drift by comparing the offset at the start vs the end.

    Cross-correlates the first and last thirds of the overlapping region; a changing
    offset means the two clocks are drifting apart over the recording.
    """
    n = min(len(a), len(b))
    if n < sr * 3:  # need a few seconds to see drift
        return None
    third = n // 3
    a0, b0 = a[:third], b[:third]
    a1, b1 = a[n - third : n], b[n - third : n]
    off0, c0 = processing.cross_correlation_offset_conf(a0, b0, sr, max_lag_s=2.0)
    off1, c1 = processing.cross_correlation_offset_conf(a1, b1, sr, max_lag_s=2.0)
    if c0 < 0.05 or c1 < 0.05:
        return None
    return (off1 - off0) / sr * 1000.0


def reverb_sustain(mono: np.ndarray, sr: int) -> float:
    """How much energy 'fills the gaps' between loud parts (0..1).

    A dry signal has quiet gaps between syllables (low median/peak ratio); reverb
    or echo fills those gaps and raises the ratio. Used to detect a reverb tail.
    """
    frame = max(int(0.02 * sr), 1)
    n_frames = len(mono) // frame
    if n_frames < 8:
        return 0.0
    rms = np.array([_rms(mono[i * frame : (i + 1) * frame]) for i in range(n_frames)])
    peak = float(np.percentile(rms, 95)) or 1e-9
    med = float(np.percentile(rms, 50))
    return float(np.clip(med / peak, 0.0, 1.0))


def duplicate_prominence(mono: np.ndarray, sr: int) -> tuple[float, float]:
    """Detect a discrete delayed COPY of the signal (stacked/duplicated audio).

    Unlike a smooth reverb decay, a literal duplicate creates a sharp secondary
    peak in the autocorrelation that stands out above the local trend. We detrend
    the envelope autocorrelation and report the strongest residual bump (prominence)
    in the 40–600 ms lag range, plus its lag. High prominence => suspicious copy.
    """
    from scipy.ndimage import uniform_filter1d

    env_rate = 400
    step = max(int(sr / env_rate), 1)
    env = np.abs(mono[::step]).astype(np.float64)
    if env.size < 16:
        return (0.0, 0.0)
    env -= env.mean()
    ac = np.correlate(env, env, mode="full")
    mid = len(ac) // 2
    zero = ac[mid] or 1.0
    ac = ac / zero
    lo = max(int(0.04 * env_rate), 2)
    hi = min(int(0.6 * env_rate), len(ac) - mid - 1)
    if hi <= lo + 4:
        return (0.0, 0.0)
    seg = ac[mid + lo : mid + hi]
    baseline = uniform_filter1d(seg, size=max(int(0.08 * env_rate), 3), mode="nearest")
    residual = seg - baseline
    k = int(np.argmax(residual))
    prominence = float(max(residual[k], 0.0))
    lag_ms = (lo + k) / env_rate * 1000.0
    return (prominence, lag_ms)


def echo_metric(mono: np.ndarray, sr: int) -> tuple[float, float]:
    """Backwards-compat shim: returns (reverb_sustain, duplicate_lag_ms)."""
    sustain = reverb_sustain(mono, sr)
    _, lag = duplicate_prominence(mono, sr)
    return (sustain, lag)


@dataclass
class ClipMetrics:
    label: str
    role: str
    duration: float
    n_samples: int
    rms_db: float
    peak_db: float
    lufs: float | None
    clip_count: int
    crest_db: float
    stereo: dict
    bands: dict
    noise_db: float
    snr_db: float
    reverb_sustain: float
    dup_prominence: float
    dup_lag_ms: float


def analyze(clip: Clip) -> ClipMetrics:
    mono = clip.mono
    peak = float(np.max(np.abs(clip.stereo))) if clip.n_samples else 0.0
    rms = _rms(mono)
    clip_count = int(np.sum(np.abs(clip.stereo) >= 0.999))
    crest_db = _dbfs(peak) - _dbfs(rms)
    noise_db, snr_db = noise_floor_and_snr(mono, clip.sr)
    sustain = reverb_sustain(mono, clip.sr)
    dup, dup_lag = duplicate_prominence(mono, clip.sr)
    return ClipMetrics(
        label=clip.label,
        role=clip.role,
        duration=clip.duration,
        n_samples=clip.n_samples,
        rms_db=_dbfs(rms),
        peak_db=_dbfs(peak),
        lufs=lufs(clip.stereo, clip.sr),
        clip_count=clip_count,
        crest_db=crest_db,
        stereo=stereo_metrics(clip.stereo),
        bands=band_energy(mono, clip.sr),
        noise_db=noise_db,
        snr_db=snr_db,
        reverb_sustain=sustain,
        dup_prominence=dup,
        dup_lag_ms=dup_lag,
    )


# --------------------------------------------------------------------------- #
# Loudness-normalised listening exports
# --------------------------------------------------------------------------- #
def normalize_for_listening(
    stereo: np.ndarray, sr: int, target_lufs: float = -16.0
) -> np.ndarray:
    """Loudness-normalise to a common target, then peak-limit to -1 dBFS.

    Ensures A/B files are judged on quality, not on whichever is louder.
    """
    out = stereo.astype(np.float32).copy()
    loud = lufs(out, sr)
    if loud is not None and np.isfinite(loud):
        gain = 10 ** ((target_lufs - loud) / 20.0)
        out = out * gain
    # Peak ceiling at -1 dBFS to avoid clipping after the loudness gain.
    peak = float(np.max(np.abs(out))) or 1.0
    ceiling = 10 ** (-1.0 / 20.0)
    if peak > ceiling:
        out = out * (ceiling / peak)
    return out.astype(np.float32)


# --------------------------------------------------------------------------- #
# Plots (returned as base64 PNG for inline HTML)
# --------------------------------------------------------------------------- #
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def plot_waveforms(clips: list[Clip]) -> str:
    fig, axes = plt.subplots(len(clips), 1, figsize=(9, 1.7 * len(clips)), sharex=True)
    if len(clips) == 1:
        axes = [axes]
    for ax, c in zip(axes, clips):
        t = np.arange(c.n_samples) / c.sr
        ax.plot(t, c.stereo[:, 0], color="#1f77b4", lw=0.4, label="L")
        ax.plot(t, c.stereo[:, 1], color="#d62728", lw=0.4, alpha=0.7, label="R")
        ax.set_ylabel(c.label, fontsize=8)
        ax.set_ylim(-1.05, 1.05)
        ax.legend(loc="upper right", fontsize=6)
    axes[-1].set_xlabel("seconds")
    fig.suptitle("Waveform comparison (L blue / R red)")
    return _fig_to_b64(fig)


def plot_spectra(clips: list[Clip]) -> str:
    fig, ax = plt.subplots(figsize=(9, 4))
    for c in clips:
        mono = c.mono
        n = len(mono)
        if n < 16:
            continue
        win = np.hanning(n)
        spec = np.abs(np.fft.rfft(mono * win))
        freqs = np.fft.rfftfreq(n, 1.0 / c.sr)
        spec_db = 20 * np.log10(spec / (spec.max() + 1e-12) + 1e-9)
        ax.semilogx(freqs[1:], spec_db[1:], lw=0.8, label=c.label)
    ax.set_xlim(20, 20000)
    ax.set_ylim(-90, 2)
    ax.set_xlabel("Hz")
    ax.set_ylabel("dB (normalised)")
    ax.set_title("Frequency spectrum comparison")
    ax.legend(fontsize=7)
    ax.grid(True, which="both", alpha=0.2)
    return _fig_to_b64(fig)


def plot_spectrograms(clips: list[Clip]) -> str:
    import warnings

    fig, axes = plt.subplots(len(clips), 1, figsize=(9, 2.2 * len(clips)), sharex=True)
    if len(clips) == 1:
        axes = [axes]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # log10(0) on silent gaps is harmless
        for ax, c in zip(axes, clips):
            ax.specgram(c.mono, NFFT=1024, Fs=c.sr, noverlap=512, cmap="magma")
            ax.set_ylabel(c.label, fontsize=8)
            ax.set_ylim(0, 12000)
    axes[-1].set_xlabel("seconds")
    fig.suptitle("Spectrogram comparison")
    return _fig_to_b64(fig)


def plot_pan_movement(clips: list[Clip], metrics: list[ClipMetrics]) -> str:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    for c, m in zip(clips, metrics):
        pans = m.stereo["pan_series"]
        if pans.size < 2:
            continue
        t = np.linspace(0, c.duration, pans.size)
        ax.plot(t, pans, lw=0.9, label=f"{c.label} (range {m.stereo['pan_range']:.2f})")
    ax.axhline(0, color="#888", lw=0.5)
    ax.set_ylim(-1.05, 1.05)
    ax.set_xlabel("seconds")
    ax.set_ylabel("pan  (-1 L .. +1 R)")
    ax.set_title("Left/right movement over time")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.2)
    return _fig_to_b64(fig)


# --------------------------------------------------------------------------- #
# Pass/fail checks
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def run_checks(
    metrics: dict[str, ClipMetrics],
    raw_offset_ms: float | None,
    drift_ms: float | None,
) -> list[Check]:
    checks: list[Check] = []
    natural = metrics.get("natural")
    raw = next((m for m in metrics.values() if m.role == "raw"), None)

    # Duration: enhanced must equal natural exactly (no stacking/drift).
    if natural is not None:
        for key, m in metrics.items():
            if m.role != "enhanced":
                continue
            same = m.n_samples == natural.n_samples
            checks.append(
                Check(
                    f"Duration unchanged — {m.label}",
                    same,
                    f"{m.n_samples} vs natural {natural.n_samples} samples "
                    f"({m.duration:.3f}s vs {natural.duration:.3f}s)",
                )
            )

    # Stereo preserved vs single phone.
    if natural is not None:
        st = natural.stereo
        moves = st["pan_range"] >= 0.15
        checks.append(
            Check(
                "Natural mix preserves L/R position",
                moves and st["correlation"] < 0.985,
                f"pan range {st['pan_range']:.2f} (>=0.15), "
                f"L/R correlation {st['correlation']:.3f} (<0.985 = not mono)",
            )
        )
        if raw is not None:
            better = st["pan_range"] >= raw.stereo["pan_range"]
            checks.append(
                Check(
                    "Stereo position better than single phone",
                    better,
                    f"natural pan range {st['pan_range']:.2f} vs "
                    f"single-phone {raw.stereo['pan_range']:.2f}",
                )
            )

    # Natural mix should not contain a strong duplicated/stacked copy.
    if natural is not None:
        ok = natural.dup_prominence < 0.30
        checks.append(
            Check(
                "Natural mix free of duplicated / stacked audio",
                ok,
                f"duplicate-peak prominence {natural.dup_prominence:.2f} at "
                f"{natural.dup_lag_ms:.0f}ms (suspicious >= 0.30)",
            )
        )

    # Studio Voice: no added reverb tail vs the natural mix (judged relative so it
    # is robust to how reverberant the underlying speech happens to be).
    studio = metrics.get("studio_voice")
    if studio is not None and natural is not None:
        no_added_reverb = studio.reverb_sustain <= natural.reverb_sustain + 0.08
        checks.append(
            Check(
                "Studio Voice adds no echo/reverb tail",
                no_added_reverb,
                f"reverb-sustain {studio.reverb_sustain:.2f} vs natural "
                f"{natural.reverb_sustain:.2f} (<= natural + 0.08)",
            )
        )
        clearer = studio.bands["high"] + studio.bands["mid"] >= (
            natural.bands["high"] + natural.bands["mid"]
        ) * 0.95
        checks.append(
            Check(
                "Studio Voice keeps/raises voice clarity",
                clearer,
                f"mid+high energy {studio.bands['mid'] + studio.bands['high']:.3f} "
                f"vs natural {natural.bands['mid'] + natural.bands['high']:.3f}",
            )
        )

    # Karaoke / Party should add a reverb tail (sustain rises) but not duplicate audio.
    for mode in ("karaoke", "party"):
        m = metrics.get(mode)
        if m is not None and natural is not None:
            has_tail = m.reverb_sustain >= natural.reverb_sustain
            checks.append(
                Check(
                    f"{mode} adds room/reverb effect",
                    has_tail,
                    f"reverb-sustain {m.reverb_sustain:.2f} vs natural "
                    f"{natural.reverb_sustain:.2f}",
                )
            )
            checks.append(
                Check(
                    f"{mode} reverb does not duplicate/stack audio",
                    m.dup_prominence < 0.45,
                    f"duplicate-peak prominence {m.dup_prominence:.2f} (< 0.45)",
                )
            )

    # Clipping on every clip.
    for m in metrics.values():
        checks.append(
            Check(
                f"No hard clipping — {m.label}",
                m.clip_count == 0,
                f"{m.clip_count} samples at full scale; peak {m.peak_db:.1f} dBFS",
            )
        )

    # Sync between raw recordings.
    if raw_offset_ms is not None:
        checks.append(
            Check(
                "Raw recordings sync offset estimated",
                True,
                f"estimated offset {raw_offset_ms:.0f} ms between raw phones",
            )
        )
    if drift_ms is not None:
        checks.append(
            Check(
                "Alignment stable over time (low drift)",
                abs(drift_ms) <= 50.0,
                f"estimated drift {drift_ms:.1f} ms across the file (<=50ms)",
            )
        )

    return checks


# --------------------------------------------------------------------------- #
# Baseline guard + plain-language comparison summary
# --------------------------------------------------------------------------- #
_BASELINE_PATH = Path(__file__).resolve().parent / "audio_baseline.json"


def load_baseline(path: Path | None = None) -> dict | None:
    """Load the frozen minimum-quality bar (audio_baseline.json), or None.

    The bench still runs without it; baseline comparison is simply skipped.
    """
    p = path or _BASELINE_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! baseline not loaded ({p.name}): {exc}")
        return None


def check_baseline(
    metrics: dict[str, ClipMetrics],
    raw_offset_ms: float | None,
    drift_ms: float | None,
    baseline: dict | None,
) -> list[Check]:
    """Compare the measured metrics against the frozen baseline thresholds.

    Returns one Check per guarded metric. A FAIL here means a regression below the
    saved working baseline (git tag v0.1.0-audio-baseline). Read-only: no audio is
    altered. Metrics/clips that are absent are simply not checked.
    """
    out: list[Check] = []
    if not baseline:
        return out

    natural = metrics.get("natural")
    ns = baseline.get("natural_stereo", {})
    if natural is not None and ns:
        st = natural.stereo
        out.append(Check(
            "Baseline · natural pan range",
            st["pan_range"] >= ns["pan_range_min"],
            f"{st['pan_range']:.2f} (>= {ns['pan_range_min']})",
        ))
        out.append(Check(
            "Baseline · natural L/R correlation (not mono)",
            st["correlation"] <= ns["lr_correlation_max"],
            f"{st['correlation']:.3f} (<= {ns['lr_correlation_max']})",
        ))
        out.append(Check(
            "Baseline · natural L/R balance",
            abs(st["balance_db"]) <= ns["lr_balance_abs_db_max"],
            f"{st['balance_db']:+.2f} dB (|x| <= {ns['lr_balance_abs_db_max']})",
        ))
        out.append(Check(
            "Baseline · natural SNR",
            natural.snr_db >= ns["snr_db_min"],
            f"{natural.snr_db:.1f} dB (>= {ns['snr_db_min']})",
        ))
        out.append(Check(
            "Baseline · natural peak headroom",
            ns["peak_dbfs_min"] <= natural.peak_db <= ns["peak_dbfs_max"],
            f"{natural.peak_db:.1f} dBFS (in [{ns['peak_dbfs_min']}, {ns['peak_dbfs_max']}])",
        ))
        out.append(Check(
            "Baseline · natural no clipping",
            natural.clip_count <= ns["clip_count_max"],
            f"{natural.clip_count} clipped samples (<= {ns['clip_count_max']})",
        ))

    dup = baseline.get("duplicate", {})
    if natural is not None and dup:
        out.append(Check(
            "Baseline · natural no duplicate/stacked audio",
            natural.dup_prominence <= dup["dup_prominence_max"],
            f"prominence {natural.dup_prominence:.2f} (<= {dup['dup_prominence_max']})",
        ))

    studio = metrics.get("studio_voice")
    sv = baseline.get("studio_voice", {})
    if studio is not None and sv:
        out.append(Check(
            "Baseline · Studio Voice SNR",
            studio.snr_db >= sv["snr_db_min"],
            f"{studio.snr_db:.1f} dB (>= {sv['snr_db_min']})",
        ))
        out.append(Check(
            "Baseline · Studio Voice keeps stereo",
            studio.stereo["correlation"] <= sv["keep_stereo_correlation_max"],
            f"corr {studio.stereo['correlation']:.3f} (<= {sv['keep_stereo_correlation_max']})",
        ))
        if natural is not None:
            limit = natural.reverb_sustain + sv["reverb_sustain_over_natural_max"]
            out.append(Check(
                "Baseline · Studio Voice adds no reverb",
                studio.reverb_sustain <= limit,
                f"sustain {studio.reverb_sustain:.2f} (<= natural + "
                f"{sv['reverb_sustain_over_natural_max']} = {limit:.2f})",
            ))

    sync = baseline.get("sync", {})
    if raw_offset_ms is not None and sync:
        out.append(Check(
            "Baseline · raw sync offset",
            raw_offset_ms <= sync["raw_offset_ms_max"],
            f"{raw_offset_ms:.0f} ms (<= {sync['raw_offset_ms_max']})",
        ))
    if drift_ms is not None and sync:
        out.append(Check(
            "Baseline · drift over time",
            abs(drift_ms) <= sync["drift_ms_max"],
            f"{drift_ms:.1f} ms (<= {sync['drift_ms_max']})",
        ))

    return out


def build_summary(
    metrics: dict[str, ClipMetrics],
    checks: list[Check],
    baseline_checks: list[Check],
) -> list[tuple[str, str, str, bool]]:
    """Plain-language answers shown at the very top of the report.

    Each item is (question, answer, detail, is_good) where answer is
    "Yes" / "No" / "—" and is_good marks whether the answer is the healthy one
    (so the UI can colour it green/red). Designed so a non-technical reader can
    judge the recording at a glance.
    """
    items: list[tuple[str, str, str, bool]] = []

    def yn(v: bool) -> str:
        return "Yes" if v else "No"

    natural = metrics.get("natural")
    raw = next((m for m in metrics.values() if m.role == "raw"), None)
    studio = metrics.get("studio_voice")

    # Q1 — Is natural better than one phone?
    if natural is not None and raw is not None:
        better = (
            natural.stereo["pan_range"] > raw.stereo["pan_range"]
            and natural.stereo["correlation"] < 0.985
        )
        items.append((
            "Is natural stereo better than one phone?",
            yn(better),
            f"pan range {natural.stereo['pan_range']:.2f} vs single-phone "
            f"{raw.stereo['pan_range']:.2f}; correlation {natural.stereo['correlation']:.3f}",
            better,
        ))
    elif natural is not None:
        items.append((
            "Is natural stereo better than one phone?",
            "—",
            "attach a single raw phone to compare",
            True,
        ))

    # Q2 — Is Studio Voice cleaner than natural?
    if studio is not None and natural is not None:
        cleaner = studio.snr_db >= natural.snr_db
        items.append((
            "Is Studio Voice cleaner than natural?",
            yn(cleaner),
            f"SNR {studio.snr_db:.1f} dB vs natural {natural.snr_db:.1f} dB; "
            f"noise floor {studio.noise_db:.1f} vs {natural.noise_db:.1f} dB",
            cleaner,
        ))

    # Q3 — Is sync acceptable?  (driven by the baseline sync checks)
    sync_checks = [c for c in baseline_checks if c.name.startswith("Baseline · raw sync")
                   or c.name.startswith("Baseline · drift")]
    if sync_checks:
        ok = all(c.passed for c in sync_checks)
        items.append((
            "Is sync acceptable?",
            yn(ok),
            "; ".join(c.detail for c in sync_checks),
            ok,
        ))

    # Q4 — Did anything fall below the saved baseline?
    if baseline_checks:
        n_below = sum(1 for c in baseline_checks if not c.passed)
        names = ", ".join(
            c.name.replace("Baseline · ", "") for c in baseline_checks if not c.passed
        )
        items.append((
            "Did anything fall below the saved baseline?",
            yn(n_below > 0),  # "Yes" here is the BAD answer
            f"{n_below} of {len(baseline_checks)} baseline checks regressed"
            + (f": {names}" if names else ""),
            n_below == 0,  # good when nothing regressed
        ))

    # Q5 — Is this recording usable for a demo?
    # Usable = natural present, no failed functional checks, no baseline regressions.
    func_fail = sum(1 for c in checks if not c.passed)
    base_fail = sum(1 for c in baseline_checks if not c.passed)
    usable = natural is not None and func_fail == 0 and base_fail == 0
    items.append((
        "Is this recording usable for a demo?",
        yn(usable),
        f"{func_fail} functional check(s) failed, {base_fail} baseline check(s) regressed",
        usable,
    ))

    return items


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #
def _fmt(v, suffix="", nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}{suffix}"
    return f"{v}{suffix}"


def build_html(
    metrics: dict[str, ClipMetrics],
    checks: list[Check],
    images: dict[str, str],
    exports: list[str],
    title: str,
    summary: list[tuple[str, str, str, bool]] | None = None,
    baseline_checks: list[Check] | None = None,
) -> str:
    summary = summary or []
    baseline_checks = baseline_checks or []
    rows = []
    for m in metrics.values():
        st = m.stereo
        rows.append(
            "<tr>"
            f"<td>{html.escape(m.label)}</td>"
            f"<td>{html.escape(m.role)}</td>"
            f"<td>{_fmt(m.duration, 's', 3)}</td>"
            f"<td>{_fmt(m.rms_db, ' dB')}</td>"
            f"<td>{_fmt(m.peak_db, ' dB')}</td>"
            f"<td>{_fmt(m.lufs, ' LUFS')}</td>"
            f"<td>{_fmt(m.crest_db, ' dB')}</td>"
            f"<td>{m.clip_count}</td>"
            f"<td>{_fmt(st['balance_db'], ' dB')}</td>"
            f"<td>{_fmt(st['correlation'], '', 3)}</td>"
            f"<td>{_fmt(st['width'], '', 2)}</td>"
            f"<td>{_fmt(st['pan_range'], '', 2)}</td>"
            f"<td>{_fmt(m.noise_db, ' dB')}</td>"
            f"<td>{_fmt(m.snr_db, ' dB')}</td>"
            f"<td>{_fmt(m.reverb_sustain, '', 2)}</td>"
            f"<td>{_fmt(m.dup_prominence, '', 2)}</td>"
            f"<td>B{_fmt(m.bands['bass'], '', 2)} "
            f"M{_fmt(m.bands['mid'], '', 2)} "
            f"H{_fmt(m.bands['high'], '', 2)}</td>"
            "</tr>"
        )
    table = "\n".join(rows)

    check_rows = []
    n_fail = 0
    for c in checks:
        if not c.passed:
            n_fail += 1
        badge = (
            '<span class="ok">PASS</span>'
            if c.passed
            else '<span class="fail">FAIL</span>'
        )
        check_rows.append(
            f"<tr><td>{badge}</td><td>{html.escape(c.name)}</td>"
            f"<td>{html.escape(c.detail)}</td></tr>"
        )
    checks_html = "\n".join(check_rows)
    verdict = (
        '<span class="ok">ALL CHECKS PASSED</span>'
        if n_fail == 0
        else f'<span class="fail">{n_fail} CHECK(S) FAILED</span>'
    )

    # Comparison summary cards (plain-language Yes/No at the very top).
    summary_cards = []
    for question, answer, detail, is_good in summary:
        cls = "card good" if is_good else "card bad"
        ans_cls = "ans ok" if is_good else "ans fail"
        summary_cards.append(
            f'<div class="{cls}"><div class="q">{html.escape(question)}</div>'
            f'<div class="{ans_cls}">{html.escape(answer)}</div>'
            f'<div class="d">{html.escape(detail)}</div></div>'
        )
    summary_html = "\n".join(summary_cards)

    # Baseline regression block.
    n_base_fail = sum(1 for c in baseline_checks if not c.passed)
    if baseline_checks:
        base_verdict = (
            '<span class="ok">AT OR ABOVE BASELINE</span>'
            if n_base_fail == 0
            else f'<span class="fail">{n_base_fail} BELOW BASELINE</span>'
        )
        base_rows = []
        for c in baseline_checks:
            badge = (
                '<span class="ok">PASS</span>' if c.passed
                else '<span class="fail">FAIL</span>'
            )
            base_rows.append(
                f"<tr><td>{badge}</td><td>{html.escape(c.name)}</td>"
                f"<td>{html.escape(c.detail)}</td></tr>"
            )
        baseline_section = (
            f'<h2>Baseline guard <span style="font-weight:400;font-size:12px;">'
            f"(vs frozen v0.1.0-audio-baseline)</span></h2>"
            f'<p class="verdict">{base_verdict}</p>'
            "<table><tr><th>Result</th><th>Baseline check</th><th>Detail</th></tr>"
            f"{chr(10).join(base_rows)}</table>"
        )
    else:
        baseline_section = (
            '<h2>Baseline guard</h2><p>No <code>audio_baseline.json</code> loaded — '
            "baseline comparison skipped.</p>"
        )

    export_list = "\n".join(
        f"<li><code>{html.escape(e)}</code></li>" for e in exports
    )

    def img_block(key: str, caption: str) -> str:
        if key not in images:
            return ""
        return (
            f'<figure><img src="data:image/png;base64,{images[key]}"/>'
            f"<figcaption>{html.escape(caption)}</figcaption></figure>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body{{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;color:#1a1a1a;}}
 h1{{font-size:20px;}} h2{{font-size:15px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px;}}
 table{{border-collapse:collapse;width:100%;font-size:12px;}}
 th,td{{border:1px solid #ddd;padding:4px 6px;text-align:left;}}
 th{{background:#f4f4f4;}}
 .ok{{color:#0a7a23;font-weight:700;}} .fail{{color:#b00020;font-weight:700;}}
 figure{{margin:14px 0;}} img{{max-width:100%;border:1px solid #eee;}}
 figcaption{{font-size:12px;color:#555;}}
 code{{background:#f4f4f4;padding:1px 4px;border-radius:3px;}}
 .verdict{{font-size:16px;margin:8px 0 16px;}}
 .cards{{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0 8px;}}
 .card{{flex:1 1 220px;border:1px solid #ddd;border-radius:8px;padding:12px 14px;
   border-left-width:6px;background:#fafafa;}}
 .card.good{{border-left-color:#0a7a23;}} .card.bad{{border-left-color:#b00020;}}
 .card .q{{font-size:12px;color:#444;margin-bottom:4px;}}
 .card .ans{{font-size:24px;font-weight:800;line-height:1;margin:2px 0 6px;}}
 .card .d{{font-size:11px;color:#666;}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="verdict">Verdict: {verdict}</p>
<p>Generated {_dt.datetime.now().isoformat(timespec='seconds')}.
LUFS {'enabled' if _HAVE_PYLN else 'unavailable (install pyloudnorm)'}.</p>

<h2>Comparison summary</h2>
<div class="cards">
{summary_html}
</div>

{baseline_section}

<h2>Pass / fail checks</h2>
<table><tr><th>Result</th><th>Check</th><th>Detail</th></tr>
{checks_html}
</table>

<h2>Metrics table</h2>
<table>
<tr><th>Clip</th><th>Role</th><th>Dur</th><th>RMS</th><th>Peak</th><th>LUFS</th>
<th>Crest</th><th>Clip</th><th>L/R bal</th><th>Corr</th><th>Width</th>
<th>Pan range</th><th>Noise</th><th>SNR</th><th>Reverb</th><th>Dup</th><th>Bands B/M/H</th></tr>
{table}
</table>

<h2>Waveforms</h2>
{img_block('waveforms', 'Waveform comparison (L blue / R red).')}
<h2>Left/right movement</h2>
{img_block('pan', 'Pan index over time — a clear sweep means detectable L→R movement.')}
<h2>Frequency spectrum</h2>
{img_block('spectra', 'Normalised spectrum — watch for lost highs / muddiness.')}
<h2>Spectrograms</h2>
{img_block('spectrograms', 'Time/frequency view per clip.')}

<h2>Normalised A/B listening files</h2>
<p>All loudness-normalised to a common target so comparison is fair:</p>
<ul>
{export_list}
</ul>
</body></html>
"""


# --------------------------------------------------------------------------- #
# File resolution
# --------------------------------------------------------------------------- #
def _resolve_from_session(session_id: str) -> dict:
    """Look up raw recordings + natural mix for a processed session via the DB."""
    from app.database import SessionLocal
    from app.models import ProcessedProject, RecordingSession
    from app.storage import get_storage, key_to_relpath
    from app.worker.tasks import _select_input_recordings

    db = SessionLocal()
    storage = get_storage()
    try:
        session = db.get(RecordingSession, session_id)
        if session is None:
            raise SystemExit(f"Session {session_id} not found.")
        recs = _select_input_recordings(db, session_id)
        raw_paths: list[str] = []
        for r in recs:
            if r.file_url:
                raw_paths.append(storage.path(key_to_relpath(r.file_url)))

        natural = None
        project: ProcessedProject | None = session.project
        if project and project.final_audio_stereo_url:
            natural = storage.path(key_to_relpath(project.final_audio_stereo_url))
        elif project and project.final_audio_url:
            natural = storage.path(key_to_relpath(project.final_audio_url))
        return {"raw": raw_paths, "natural": natural}
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
# Friendly listening-export file names per role/label.
_EXPORT_NAMES: dict[str, str] = {
    "single_phone": "A_single_phone_normalized.wav",
    "natural": "B_natural_stereo_mix_normalized.wav",
    "studio_voice": "C_studio_voice_normalized.wav",
    "karaoke": "D_karaoke_normalized.wav",
    "party": "E_party_normalized.wav",
    "mono_downmix": "F_mono_downmix_normalized.wav",
}


def build_report(
    clips: list[Clip],
    out_dir: Path,
    target_lufs: float = -16.0,
    title: str = "Audio Test Bench",
) -> dict:
    """Analyse whatever clips are provided and write report.html + A/B wavs.

    Only the clips passed in appear in the report — if a role/file was not
    attached, it is simply absent. Returns a small summary dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if not clips:
        raise ValueError("No clips to analyse.")

    # Metrics for every clip.
    metrics: dict[str, ClipMetrics] = {c.label: analyze(c) for c in clips}

    # Sync analysis between the first two raw recordings (offset + drift).
    raw_offset_ms: float | None = None
    drift_ms: float | None = None
    raw_clips = [c for c in clips if c.role == "raw"]
    if len(raw_clips) >= 2:
        try:
            tracks = [
                processing.LoadedTrack(c.label, c.mono, c.sr, None)
                for c in raw_clips[:2]
            ]
            offs = processing.compute_offsets(tracks)
            vals = list(offs.values())
            raw_offset_ms = abs(vals[0] - vals[1]) / raw_clips[0].sr * 1000.0
            drift_ms = estimate_drift_ms(
                raw_clips[0].mono, raw_clips[1].mono, raw_clips[0].sr
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! sync analysis failed: {exc}")

    # Normalised A/B listening exports — only for clips that are present.
    exports: list[str] = []
    for c in clips:
        fname = _EXPORT_NAMES.get(c.label, f"{c.label}_normalized.wav")
        norm = normalize_for_listening(c.stereo, c.sr, target_lufs)
        sf.write(str(out_dir / fname), norm, c.sr, subtype="PCM_16")
        exports.append(fname)

    # Plots (built only from the clips provided).
    images = {
        "waveforms": plot_waveforms(clips),
        "pan": plot_pan_movement(clips, [metrics[c.label] for c in clips]),
        "spectra": plot_spectra(clips),
        "spectrograms": plot_spectrograms(clips),
    }

    checks = run_checks(metrics, raw_offset_ms, drift_ms)
    baseline = load_baseline()
    baseline_checks = check_baseline(metrics, raw_offset_ms, drift_ms, baseline)
    summary = build_summary(metrics, checks, baseline_checks)
    report = build_html(
        metrics, checks, images, exports, title,
        summary=summary, baseline_checks=baseline_checks,
    )
    report_path = out_dir / "report.html"
    report_path.write_text(report, encoding="utf-8")

    n_fail = sum(1 for c in checks if not c.passed)
    n_base_fail = sum(1 for c in baseline_checks if not c.passed)
    return {
        "report_path": str(report_path.resolve()),
        "clips": [c.label for c in clips],
        "exports": exports,
        "checks_total": len(checks),
        "checks_failed": n_fail,
        "checks": [(c.passed, c.name, c.detail) for c in checks],
        "baseline_total": len(baseline_checks),
        "baseline_failed": n_base_fail,
        "baseline_checks": [(c.passed, c.name, c.detail) for c in baseline_checks],
        "summary": [
            {"question": q, "answer": a, "detail": d, "good": g}
            for (q, a, d, g) in summary
        ],
    }


def collect_clips(
    raw_paths: list[str],
    natural_path: str | None,
    *,
    include_enhanced: bool = True,
    include_mono_downmix: bool = True,
) -> list[Clip]:
    """Load the requested files into clips. Missing inputs are skipped silently."""
    clips: list[Clip] = []

    # Raw recordings — the first is the single-phone reference.
    for i, p in enumerate(raw_paths):
        if not p:
            continue
        try:
            label = "single_phone" if i == 0 else f"raw_phone_{i + 1}"
            clips.append(load_clip(label, p, "raw"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! could not load raw '{p}': {exc}")

    # Natural stereo mix.
    natural_clip: Clip | None = None
    if natural_path:
        try:
            natural_clip = load_clip("natural", natural_path, "natural")
            clips.append(natural_clip)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! could not load natural '{natural_path}': {exc}")

    # Enhanced presets + mono down-mix derived from the natural mix.
    if natural_clip is not None and include_enhanced:
        for mode in effects.ENHANCEMENT_MODES:
            if mode == effects.DEFAULT_MODE:
                continue
            enhanced = effects.apply_enhancement(
                natural_clip.stereo, natural_clip.sr, mode
            )
            clips.append(
                Clip(mode, enhanced, natural_clip.sr, "enhanced", "(generated)")
            )
    if natural_clip is not None and include_mono_downmix:
        mono = natural_clip.mono
        mono_st = np.column_stack([mono, mono]).astype(np.float32)
        clips.append(Clip("mono_downmix", mono_st, natural_clip.sr, "mono_downmix"))

    return clips


def main() -> None:
    ap = argparse.ArgumentParser(description="Audio test bench / quality report.")
    ap.add_argument("--session-id", help="Resolve files from the database.")
    ap.add_argument("--raw", action="append", default=[], help="Raw recording file(s).")
    ap.add_argument("--natural", help="Natural stereo mix file (final_mix_stereo.wav).")
    ap.add_argument("--out", default="bench_out", help="Output directory.")
    ap.add_argument(
        "--target-lufs",
        type=float,
        default=-16.0,
        help="Loudness target for the normalised A/B files.",
    )
    args = ap.parse_args()

    raw_paths: list[str] = list(args.raw)
    natural_path: str | None = args.natural

    if args.session_id:
        resolved = _resolve_from_session(args.session_id)
        raw_paths = raw_paths or resolved["raw"]
        natural_path = natural_path or resolved["natural"]

    if not raw_paths and not natural_path:
        raise SystemExit(
            "Nothing to analyse. Provide --session-id, or --raw/--natural files."
        )

    clips = collect_clips(raw_paths, natural_path)
    if not clips:
        raise SystemExit("No clips could be loaded.")
    print(f"Loaded {len(clips)} clip(s): {[c.label for c in clips]}")

    title = "Audio Test Bench — " + (args.session_id or "file comparison")
    result = build_report(clips, Path(args.out), args.target_lufs, title)

    print(f"Wrote {len(result['exports'])} normalised A/B file(s).")
    print(f"\nReport: {result['report_path']}")

    # Plain-language comparison summary first (the headline answers).
    print("\nComparison summary:")
    for item in result.get("summary", []):
        print(f"  {item['answer']:>3}  {item['question']}")

    # Baseline guard verdict.
    if result.get("baseline_total", 0):
        print(
            f"\nBaseline guard: {result['baseline_total'] - result['baseline_failed']}"
            f" at/above baseline, {result['baseline_failed']} below."
        )
        for passed, name, detail in result["baseline_checks"]:
            if not passed:
                print(f"  [BELOW] {name} — {detail}")

    print(
        f"\nChecks: {result['checks_total'] - result['checks_failed']} passed, "
        f"{result['checks_failed']} failed."
    )
    for passed, name, detail in result["checks"]:
        flag = "PASS" if passed else "FAIL"
        print(f"  [{flag}] {name} — {detail}")


if __name__ == "__main__":
    main()
