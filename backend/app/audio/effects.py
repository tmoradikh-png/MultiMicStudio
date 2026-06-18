"""Optional audio enhancement presets applied ON TOP of the natural stereo mix.

Design rules (from product feedback):
  * Stereo separation / spatial positioning must be preserved — never collapse to mono.
  * Effects are OPTIONAL and selectable; the raw natural stereo is always kept too.
  * Heavy reverb/echo must not be used to mask sync problems, so defaults stay light.

Every preset takes and returns a stereo array shaped (N, 2), float32, and never
changes the length (so it cannot introduce stacking/duplication or timing drift).
All processing is vectorised (scipy.signal.lfilter / sosfilt) so it stays fast.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

# Public list of selectable modes (kept in sync with the API/UI).
ENHANCEMENT_MODES = ("natural", "studio_voice", "podcast", "karaoke", "party")
DEFAULT_MODE = "natural"


# --- small building blocks ---------------------------------------------------

def _as_stereo(x: np.ndarray) -> np.ndarray:
    """Coerce input to an (N, 2) float32 stereo array without losing channels."""
    a = np.asarray(x, dtype=np.float32)
    if a.ndim == 1:
        a = np.column_stack([a, a])
    elif a.shape[1] == 1:
        a = np.repeat(a, 2, axis=1)
    return a.astype(np.float32)


def _highpass(x: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Remove low-frequency rumble/handling noise (per channel)."""
    sos = signal.butter(2, cutoff_hz / (sr / 2), btype="highpass", output="sos")
    return signal.sosfilt(sos, x, axis=0).astype(np.float32)


def _biquad_peak(x: np.ndarray, sr: int, f0: float, q: float, gain_db: float) -> np.ndarray:
    """RBJ peaking EQ filter (boost/cut a band), applied per channel."""
    a = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * f0 / sr
    alpha = np.sin(w0) / (2 * q)
    cos = np.cos(w0)
    b = [1 + alpha * a, -2 * cos, 1 - alpha * a]
    a_ = [1 + alpha / a, -2 * cos, 1 - alpha / a]
    b = np.array(b) / a_[0]
    a_ = np.array(a_) / a_[0]
    return signal.lfilter(b, a_, x, axis=0).astype(np.float32)


def _compressor(
    x: np.ndarray,
    sr: int,
    threshold_db: float = -22.0,
    ratio: float = 3.0,
    attack_ms: float = 8.0,
    release_ms: float = 140.0,
    makeup_db: float = 0.0,
) -> np.ndarray:
    """Feed-forward compressor with a STEREO-LINKED gain.

    A single gain (driven by the louder channel) is applied to both channels so the
    left/right balance — and therefore the spatial image — is preserved exactly.
    """
    eps = 1e-9
    detector = np.max(np.abs(x), axis=1) + eps  # link channels
    level_db = 20 * np.log10(detector)
    over = np.maximum(0.0, level_db - threshold_db)
    gain_db = -over * (1.0 - 1.0 / ratio)

    # Smooth the gain with attack/release one-pole envelopes.
    atk = np.exp(-1.0 / (sr * attack_ms / 1000.0))
    rel = np.exp(-1.0 / (sr * release_ms / 1000.0))
    smoothed = np.empty_like(gain_db)
    g = 0.0
    for i, target in enumerate(gain_db):
        coeff = atk if target < g else rel
        g = coeff * g + (1 - coeff) * target
        smoothed[i] = g
    lin = (10 ** ((smoothed + makeup_db) / 20.0)).astype(np.float32)
    return (x * lin[:, None]).astype(np.float32)


def _noise_gate(
    x: np.ndarray, sr: int, threshold_db: float = -50.0, floor_db: float = -18.0
) -> np.ndarray:
    """Gently attenuate very quiet sections (breath/hiss) without hard chopping.

    Uses a stereo-linked smoothed envelope so both channels duck together, keeping
    the stereo image intact. `floor_db` caps how much is removed to avoid pumping.
    """
    eps = 1e-9
    env = np.max(np.abs(x), axis=1) + eps
    env_db = 20 * np.log10(env)
    # Smooth envelope (~30 ms) so the gate reacts musically.
    coeff = np.exp(-1.0 / (sr * 0.03))
    sm = np.empty_like(env_db)
    e = env_db[0]
    for i, v in enumerate(env_db):
        e = coeff * e + (1 - coeff) * v
        sm[i] = e
    # Below the threshold, attenuate by up to `floor_db`; above it, pass through.
    reduction_db = np.where(sm < threshold_db, floor_db, 0.0)
    lin = (10 ** (reduction_db / 20.0)).astype(np.float32)
    return (x * lin[:, None]).astype(np.float32)


def _denoise_gate(
    x: np.ndarray,
    sr: int,
    over_floor_db: float = 9.0,
    ratio: float = 2.5,
    min_gain_db: float = -16.0,
) -> np.ndarray:
    """Adaptive downward expander that suppresses the noise floor before any boost.

    Unlike :func:`_noise_gate` (a fixed-threshold ducker), this measures the actual
    per-clip noise floor and only attenuates material *near* that floor, leaving real
    speech untouched. Running it BEFORE EQ/compression is what keeps Studio Voice
    from amplifying hiss: the noise is pulled down first, so later make-up gain and
    presence EQ lift the voice without raising the background.

    Stereo-linked (one gain for both channels) so the spatial image is preserved.
    Length is never changed.
    """
    eps = 1e-9
    # Stereo-linked short-term RMS envelope (~20 ms).
    win = max(1, int(0.02 * sr))
    mono = np.max(np.abs(x), axis=1)
    env = np.sqrt(
        signal.lfilter(np.ones(win) / win, [1.0], mono.astype(np.float64) ** 2) + eps
    )
    env_db = 20 * np.log10(env + eps)
    # Noise floor = low percentile of the envelope; gate opens this far above it.
    floor_db = float(np.percentile(env_db, 10))
    thresh_db = floor_db + over_floor_db
    # Below threshold: expand downward (ratio); above: unity.
    under = np.maximum(0.0, thresh_db - env_db)
    gain_db = np.clip(-under * (ratio - 1.0), min_gain_db, 0.0)
    # Smooth the gain (~25 ms) to avoid zipper noise / pumping.
    coeff = np.exp(-1.0 / (sr * 0.025))
    sm = np.empty_like(gain_db)
    g = 0.0
    for i, v in enumerate(gain_db):
        # fast release toward 0 (open), slower attack toward attenuation
        coeff_i = coeff if v < g else np.exp(-1.0 / (sr * 0.006))
        g = coeff_i * g + (1 - coeff_i) * v
        sm[i] = g
    lin = (10 ** (sm / 20.0)).astype(np.float32)
    return (x * lin[:, None]).astype(np.float32)


def _comb(x: np.ndarray, delay: int, gain: float) -> np.ndarray:
    """Feedback comb filter (IIR): y[n] = x[n] + gain * y[n-delay]."""
    a = np.zeros(delay + 1, dtype=np.float32)
    a[0] = 1.0
    a[delay] = -gain
    return signal.lfilter([1.0], a, x, axis=0).astype(np.float32)


def _allpass(x: np.ndarray, delay: int, gain: float) -> np.ndarray:
    """Schroeder allpass filter for diffusion."""
    b = np.zeros(delay + 1, dtype=np.float32)
    b[0] = -gain
    b[delay] = 1.0
    a = np.zeros(delay + 1, dtype=np.float32)
    a[0] = 1.0
    a[delay] = -gain
    return signal.lfilter(b, a, x, axis=0).astype(np.float32)


def _reverb(x: np.ndarray, sr: int, wet: float, room: float = 0.84) -> np.ndarray:
    """Schroeder reverb (4 combs + 2 allpass) with slightly different L/R delays.

    The small left/right delay offset keeps the reverb tail wide instead of mono,
    so spatial positioning survives the effect.
    """
    if wet <= 0:
        return x
    # Comb delays in samples (classic Schroeder tunings scaled to sr), L and R differ.
    base = np.array([1116, 1188, 1277, 1356])
    combs_l = (base * sr / 44100).astype(int)
    combs_r = ((base + 23) * sr / 44100).astype(int)  # detune R for width
    ap = (np.array([556, 441]) * sr / 44100).astype(int)

    def one(channel: np.ndarray, combs: np.ndarray) -> np.ndarray:
        acc = np.zeros_like(channel)
        for d in combs:
            acc += _comb(channel, int(d), room)
        acc /= len(combs)
        for d in ap:
            acc = _allpass(acc, int(d), 0.5)
        return acc

    wet_l = one(x[:, 0], combs_l)
    wet_r = one(x[:, 1], combs_r)
    wet_sig = np.column_stack([wet_l, wet_r]).astype(np.float32)
    # Tame the wet level before mixing.
    peak = float(np.max(np.abs(wet_sig))) + 1e-9
    if peak > 1.0:
        wet_sig /= peak
    return ((1 - wet) * x + wet * wet_sig).astype(np.float32)


def _echo(x: np.ndarray, sr: int, delay_ms: float, wet: float, feedback: float = 0.25) -> np.ndarray:
    """Single-tap delay/echo with light feedback (per channel, preserves stereo)."""
    if wet <= 0:
        return x
    d = max(1, int(delay_ms / 1000.0 * sr))
    a = np.zeros(d + 1, dtype=np.float32)
    a[0] = 1.0
    a[d] = -feedback
    delayed = signal.lfilter([1.0], a, x, axis=0).astype(np.float32)
    return (x + wet * delayed).astype(np.float32)


def _widen(x: np.ndarray, amount: float) -> np.ndarray:
    """Mid/side stereo widening. amount>1 widens; mid (mono) is untouched."""
    mid = (x[:, 0] + x[:, 1]) * 0.5
    side = (x[:, 0] - x[:, 1]) * 0.5 * amount
    left = mid + side
    right = mid - side
    return np.column_stack([left, right]).astype(np.float32)


def _normalize(x: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """Peak-normalize then soft-limit so output is consistent but never clips."""
    peak = float(np.max(np.abs(x))) + 1e-9
    if peak > 0:
        x = x * (target_peak / peak)
    # Soft limiter only on the parts that would exceed full scale.
    over = np.abs(x) > 1.0
    if np.any(over):
        x = np.tanh(x).astype(np.float32)
    return x.astype(np.float32)


# --- presets -----------------------------------------------------------------

def apply_enhancement(stereo: np.ndarray, sr: int, mode: str) -> np.ndarray:
    """Apply the named enhancement preset to a stereo signal.

    Returns a new (N, 2) float32 array of the SAME length. Unknown modes and
    "natural" return the signal essentially untouched (reference for comparison).
    """
    x = _as_stereo(stereo)
    mode = (mode or DEFAULT_MODE).lower()

    if mode == "natural" or mode not in ENHANCEMENT_MODES:
        # Natural: keep true stereo, only safety peak-normalize. Minimal processing.
        return _normalize(x, target_peak=0.97)

    if mode == "studio_voice":
        # Cleaner, fuller voice. ORDER MATTERS for SNR: suppress the noise floor
        # FIRST (adaptive expander), THEN shape/boost, so presence EQ and make-up
        # gain lift the voice without raising the background hiss.
        x = _highpass(x, sr, 85.0)
        x = _denoise_gate(x, sr, over_floor_db=10.0, ratio=2.5, min_gain_db=-16.0)
        x = _biquad_peak(x, sr, 220.0, 1.0, -1.5)   # reduce boxiness
        x = _biquad_peak(x, sr, 3000.0, 0.9, 2.5)   # presence/intelligibility
        x = _biquad_peak(x, sr, 8000.0, 0.8, 1.0)   # gentle air (less hiss lift)
        x = _compressor(x, sr, threshold_db=-20.0, ratio=2.5, makeup_db=1.5)
        # Final light expander keeps the floor down after make-up gain.
        x = _denoise_gate(x, sr, over_floor_db=8.0, ratio=2.0, min_gain_db=-10.0)
        return _normalize(x, target_peak=0.85)  # ~-1.4 dBFS headroom

    if mode == "podcast":
        # Clean spoken-voice preset: stronger, broadband noise suppression and a
        # tighter band than studio_voice, for talking/podcast clarity. No reverb or
        # echo (those would muddy speech). Length-preserving like every preset.
        x = _highpass(x, sr, 100.0)
        x = _denoise_gate(x, sr, over_floor_db=12.0, ratio=3.0, min_gain_db=-18.0)
        x = _biquad_peak(x, sr, 300.0, 1.0, -2.0)    # cut mud/boom
        x = _biquad_peak(x, sr, 2500.0, 0.9, 2.0)    # intelligibility
        x = _biquad_peak(x, sr, 6500.0, 0.8, -1.5)   # tame sibilance/hiss
        x = _compressor(x, sr, threshold_db=-18.0, ratio=3.0, makeup_db=2.0)
        x = _denoise_gate(x, sr, over_floor_db=9.0, ratio=2.5, min_gain_db=-12.0)
        return _normalize(x, target_peak=0.85)  # ~-1.4 dBFS headroom

    if mode == "karaoke":
        # Singer vocal effect: clean first, then EQ + compression + reverb + echo.
        x = _highpass(x, sr, 90.0)
        x = _denoise_gate(x, sr, over_floor_db=9.0, ratio=2.0, min_gain_db=-12.0)
        x = _biquad_peak(x, sr, 3000.0, 0.9, 2.5)
        x = _compressor(x, sr, threshold_db=-20.0, ratio=3.5, makeup_db=2.0)
        x = _reverb(x, sr, wet=0.22, room=0.82)     # not too strong by default
        x = _echo(x, sr, delay_ms=130.0, wet=0.14, feedback=0.22)
        return _normalize(x, target_peak=0.85)  # ~-1.4 dBFS headroom

    if mode == "party":
        # Fun/live sound: clean first so widening/reverb don't amplify hiss.
        x = _denoise_gate(x, sr, over_floor_db=9.0, ratio=2.0, min_gain_db=-12.0)
        x = _compressor(x, sr, threshold_db=-20.0, ratio=2.5, makeup_db=2.0)
        x = _widen(x, amount=1.6)
        x = _reverb(x, sr, wet=0.32, room=0.86)
        return _normalize(x, target_peak=0.85)  # ~-1.4 dBFS headroom

    return _normalize(x, target_peak=0.97)
