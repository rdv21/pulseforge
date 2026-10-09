"""DSP processors for native Python mic processing.

Implemented from scratch with numpy — no scipy dependency.

Processors:
  - Gate: threshold-based expander with attack/release (manual or auto-threshold)
  - EQ: 8-band peaking biquad filter
  - Compressor: feed-forward compressor with attack/release
  - Limiter: output peak limiter with ceiling
  - AmbientNoiseReduction: adaptive FFT spectral-subtraction NR
  - MultibandCompressor: Linkwitz-Riley crossover multiband compressor

All processors operate on numpy float32 arrays (mono or stereo).
Sample rate: 48000 Hz.
"""
import numpy as np
import ctypes
import ctypes.util
import subprocess
import os
from pathlib import Path

SAMPLE_RATE = 48000


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _linear_to_db(lin: float) -> float:
    return 20.0 * np.log10(max(lin, 1e-10))


# ─── Gate ──────────────────────────────────────────────────────────

class GateProcessor:
    """Block-level gate with smooth gain ramping.

    Computes one decision per block (480 samples = 10ms) and smoothly
    ramps gain between blocks. Much faster than per-sample processing
    and avoids choppy artifacts from Python loop overhead.
    """

    def __init__(self, threshold_db: float = -35.0, enabled: bool = True,
                 attack_ms: float = 25.0, hold_ms: float = 300.0, release_ms: float = 200.0,
                 range_db: float = -25.0, auto_threshold: bool = False, offset_db: float = 12.0):
        # RMS-based detection: speech RMS is ~12dB below peak, so lower
        # the RMS threshold by 12dB to match the same perceptual opening point.
        self._rms_offset_db = -12.0
        self._threshold_db = threshold_db
        self.threshold = _db_to_linear(threshold_db + self._rms_offset_db)
        self._gate_open = False
        self.enabled = enabled
        # Auto-threshold: track the noise floor and open at
        # floor + offset, so the gate adapts to the room/mic without manual tuning.
        self._auto_threshold = auto_threshold
        self._offset_db = offset_db
        self._floor_db = -80.0
        # Range: how much the gate attenuates when closed.
        # -25dB = strong attenuation but not dead silence (natural breath ambience)
        # 0.0 = complete cutoff (unnatural, causes abrupt fadeouts)
        self.range = _db_to_linear(range_db)  # e.g. -25dB → ~0.056
        self._range_db = range_db
        self._attack_ms = attack_ms
        self._hold_ms = hold_ms
        self._release_ms = release_ms
        self._attack_samples = max(1, int(attack_ms * 0.001 * SAMPLE_RATE))
        self._hold_samples = int(hold_ms * 0.001 * SAMPLE_RATE)
        self._release_samples = max(1, int(release_ms * 0.001 * SAMPLE_RATE))
        self._gain = self.range  # start gated (at range, not 0)
        self._target_gain = self.range
        self._hold_counter = 0
        self._ramp_per_sample = 0.0  # how much gain changes per sample
        # Pre-allocated buffers — reused across process() calls
        self._gain_curve = np.empty(480, dtype=np.float32)
        self._indices = np.arange(480, dtype=np.float32)

    def set_threshold(self, threshold_db: float):
        self._threshold_db = threshold_db
        self.threshold = _db_to_linear(threshold_db + self._rms_offset_db)

    def set_auto_threshold(self, auto: bool):
        """Enable adaptive noise-floor tracking (threshold = floor + offset)."""
        self._auto_threshold = bool(auto)
        if not auto:
            # revert to the manual threshold
            self.threshold = _db_to_linear(self._threshold_db + self._rms_offset_db)

    def set_offset_db(self, offset_db: float):
        """How far above the tracked noise floor the gate opens (dB)."""
        self._offset_db = float(offset_db)

    def _update_auto_threshold(self, rms: float):
        """Track the noise floor with fast-down / slow-up asymmetry."""
        rms_db = 20.0 * np.log10(max(rms, 1e-9))
        if rms_db < self._floor_db:
            # signal is quieter than the floor — track down quickly
            self._floor_db += 0.5 * (rms_db - self._floor_db)
        else:
            # signal is louder — let the floor creep up slowly
            self._floor_db += 0.003 * (rms_db - self._floor_db)
        self._floor_db = max(-100.0, min(0.0, self._floor_db))
        eff_db = self._floor_db + self._offset_db + self._rms_offset_db
        self.threshold = _db_to_linear(eff_db)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def set_range(self, range_db: float):
        """Set gate floor — how much attenuation when closed (dB below unity)."""
        self._range_db = range_db
        self.range = _db_to_linear(range_db)
        # If currently gated, update current gain to new floor
        if self._target_gain <= self.range * 1.1:
            self._gain = self.range

    def set_attack(self, attack_ms: float):
        self._attack_ms = attack_ms
        self._attack_samples = max(1, int(attack_ms * 0.001 * SAMPLE_RATE))

    def set_hold(self, hold_ms: float):
        self._hold_ms = hold_ms
        self._hold_samples = int(hold_ms * 0.001 * SAMPLE_RATE)

    def set_release(self, release_ms: float):
        self._release_ms = release_ms
        self._release_samples = max(1, int(release_ms * 0.001 * SAMPLE_RATE))

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return block

        # Block-level RMS detection (more robust than peak for gate decisions)
        rms = float(np.sqrt(np.mean(block ** 2)))
        peak = float(np.max(np.abs(block)))

        if self._auto_threshold:
            self._update_auto_threshold(rms)

        # Gate state machine — RMS-based with hold timer
        if rms > self.threshold:
            self._target_gain = 1.0
            self._hold_counter = self._hold_samples
        elif self._hold_counter > 0:
            # Still in hold period
            self._hold_counter -= len(block)
            if self._hold_counter < 0:
                self._hold_counter = 0
        else:
            # Below threshold, hold expired — close
            self._target_gain = self.range
        # Choose ramp speed based on direction (open vs close)
        if self._target_gain > self._gain:
            # Opening — use attack time
            ramp_len = self._attack_samples
        else:
            # Closing — use release time
            ramp_len = self._release_samples

        # Linear ramp from current gain to target over ramp_len samples
        block_len = len(block)
        # Ensure pre-allocated buffers are large enough
        if block_len > len(self._gain_curve):
            self._gain_curve = np.empty(block_len, dtype=np.float32)
            self._indices = np.arange(block_len, dtype=np.float32)
        gc = self._gain_curve[:block_len]
        if ramp_len <= 1:
            self._gain = self._target_gain
            gc.fill(self._gain)
        else:
            # Compute per-sample gain using linear interpolation (in-place on pre-alloc'd buffer)
            start_gain = self._gain
            delta = (self._target_gain - start_gain) / ramp_len
            # Generate ramp using pre-allocated indices
            idx = self._indices[:block_len]
            np.multiply(delta, idx, out=gc)
            gc += start_gain
            # Clamp to target if we've reached it within this block
            if delta > 0:
                np.minimum(gc, self._target_gain, out=gc)
            else:
                np.maximum(gc, self._target_gain, out=gc)
            # Update _gain to where we end up after this block
            self._gain = float(gc[-1])

        if not np.isfinite(self._gain):
            self._gain = 1.0

        # block * gc creates a NEW array (numpy binary op) — safe to return
        # Reshape gc for broadcasting: (block_len,) -> (block_len, 1) for stereo
        if block.ndim == 2:
            return block * gc[:, np.newaxis]
        return block * gc


# ─── Biquad Peaking Filter (for EQ) ────────────────────────────────

class EMIFilter:
    """FFT-based notch filter for USB EMI / electrical interference.

    Removes tonal noise at a fundamental frequency and its harmonics
    using frequency-domain zeroing. One FFT per block — much faster
    than cascaded per-sample biquad notch filters.

    Block size = 480 at 48kHz -> bin width = 100Hz.
    240Hz falls near bin 2-3 -> zero both +/- 1 bin.
    """

    def __init__(self, fundamental_hz=240.0, num_harmonics=2, bin_radius=1, sample_rate=48000, block_size=480):
        self.enabled = True
        self._block_size = block_size
        bin_hz = sample_rate / block_size
        self._zero_bins = set()
        for h in range(1, num_harmonics + 1):
            center = round(fundamental_hz * h / bin_hz)
            for b in range(center - bin_radius, center + bin_radius + 1):
                if 0 < b < block_size // 2:
                    self._zero_bins.add(b)

    def set_enabled(self, enabled):
        self.enabled = enabled

    def process(self, block):
        if not self.enabled or len(block) != self._block_size:
            return block
        spectrum = np.fft.rfft(block.astype(np.float64))
        for b in self._zero_bins:
            spectrum[b] = 0.0
        out = np.fft.irfft(spectrum, n=self._block_size)
        return out.astype(np.float32)


class BiquadNotch:
    """Second-order notch (band-stop) biquad filter.

    Removes a narrow frequency band — ideal for tonal noise
    (coil whine, USB EMI, mains hum harmonics) without affecting
    surrounding frequencies. Higher Q = narrower notch.
    """

    def __init__(self, freq: float, q: float = 15.0, sample_rate: int = SAMPLE_RATE):
        self._set_params(freq, q, sample_rate)
        self._x1l = self._x2l = self._y1l = self._y2l = 0.0

    def _set_params(self, freq: float, q: float, sr: int):
        w0 = 2.0 * np.pi * freq / sr
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * max(q, 0.1))

        b0 = 1.0
        b1 = -2.0 * cos_w0
        b2 = 1.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        self._b0 = np.float32(b0 / a0)
        self._b1 = np.float32(b1 / a0)
        self._b2 = np.float32(b2 / a0)
        self._a1 = np.float32(a1 / a0)
        self._a2 = np.float32(a2 / a0)

    def set_freq(self, freq: float, q: float = 15.0):
        self._set_params(freq, q, SAMPLE_RATE)

    def _process_channel(self, block: np.ndarray, state: tuple) -> tuple:
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        x1, x2, y1, y2 = state

        ff = b0 * block.copy()
        ff[1:] += b1 * block[:-1]
        if len(block) > 1:
            ff[2:] += b2 * block[:-2]
        ff[0] += b1 * x1 + b2 * x2
        if len(block) > 1:
            ff[1] += b2 * x1

        out = np.empty_like(block)
        for i in range(len(block)):
            y = ff[i] - a1 * y1 - a2 * y2
            out[i] = y
            y2 = y1
            y1 = y

        return out, (block[-1] if len(block) > 0 else x1,
                     block[-2] if len(block) > 1 else x1,
                     float(out[-1]) if len(out) > 0 else y1,
                     float(out[-2]) if len(out) > 1 else y2)

    def process(self, block: np.ndarray) -> np.ndarray:
        if block.ndim == 1:
            out, (self._x1l, self._x2l, self._y1l, self._y2l) = \
                self._process_channel(block, (self._x1l, self._x2l, self._y1l, self._y2l))
            return out
        out_l, (self._x1l, self._x2l, self._y1l, self._y2l) = \
            self._process_channel(block[:, 0], (self._x1l, self._x2l, self._y1l, self._y2l))
        return out_l


class NotchFilterChain:
    """Cascaded biquad notch filters for removing multiple tonal noise frequencies.

    Each notch targets a specific frequency with configurable Q.
    Processes mono blocks; stereo is squeezed to mono.
    """

    def __init__(self, freqs: list[float] = None, q: float = 15.0,
                 sample_rate: int = SAMPLE_RATE, block_size: int = 480):
        self.enabled = True
        self._filters: list[BiquadNotch] = []
        self._freqs = freqs or []
        self._q = q
        self._sample_rate = sample_rate
        self._block_size = block_size
        for f in self._freqs:
            self._filters.append(BiquadNotch(f, q, sample_rate))

    def set_frequencies(self, freqs: list[float], q: float = 15.0):
        """Replace all notch frequencies."""
        self._freqs = freqs
        self._q = q
        self._filters = [BiquadNotch(f, q, self._sample_rate) for f in freqs]

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled or not self._filters:
            return block
        if block.ndim == 2:
            block = block.mean(axis=1)
        for f in self._filters:
            block = f.process(block)
        return block


class BiquadHighPass:
    """Second-order high-pass biquad filter for rumble/cable noise removal.

    Uses Direct Form I with vectorized feed-forward.
    """

    def __init__(self, freq: float, q: float = 0.707, sample_rate: int = SAMPLE_RATE):
        self._set_params(freq, q, sample_rate)
        self._x1l = self._x2l = self._y1l = self._y2l = 0.0

    def _set_params(self, freq: float, q: float, sr: int):
        w0 = 2.0 * np.pi * freq / sr
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * max(q, 0.1))

        b0 = (1 + cos_w0) / 2
        b1 = -(1 + cos_w0)
        b2 = (1 + cos_w0) / 2
        a0 = 1 + alpha
        a1 = -2 * cos_w0
        a2 = 1 - alpha

        self._b0 = np.float32(b0/a0)
        self._b1 = np.float32(b1/a0)
        self._b2 = np.float32(b2/a0)
        self._a1 = np.float32(a1/a0)
        self._a2 = np.float32(a2/a0)

    def set_freq(self, freq: float, q: float = 0.707):
        self._set_params(freq, q, SAMPLE_RATE)

    def _process_channel(self, block: np.ndarray, state: tuple) -> tuple:
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        x1, x2, y1, y2 = state

        ff = b0 * block.copy()
        ff[1:] += b1 * block[:-1]
        if len(block) > 1:
            ff[2:] += b2 * block[:-2]
        ff[0] += b1 * x1 + b2 * x2
        if len(block) > 1:
            ff[1] += b2 * x1

        out = np.empty_like(block)
        for i in range(len(block)):
            y = ff[i] - a1 * y1 - a2 * y2
            out[i] = y
            y2 = y1
            y1 = y

        return out, (block[-1] if len(block) > 0 else x1,
                     block[-2] if len(block) > 1 else x1,
                     float(out[-1]) if len(out) > 0 else y1,
                     float(out[-2]) if len(out) > 1 else y2)

    def process(self, block: np.ndarray) -> np.ndarray:
        if block.ndim == 1:
            out, (self._x1l, self._x2l, self._y1l, self._y2l) = \
                self._process_channel(block, (self._x1l, self._x2l, self._y1l, self._y2l))
            return out
        out_l, (self._x1l, self._x2l, self._y1l, self._y2l) = \
            self._process_channel(block[:, 0], (self._x1l, self._x2l, self._y1l, self._y2l))
        return out_l  # mono only for now


class BiquadPeak:
    """Single peaking biquad filter (Direct Form I).

    Uses numpy vectorized processing for the feed-forward part (b coefficients)
    and a tight Python loop only for the recursive part (a coefficients).
    This is ~5-10x faster than full per-sample Python for 480-sample blocks.
    """

    def __init__(self, freq: float, gain_db: float, q: float, sample_rate: int = SAMPLE_RATE):
        self._set_params(freq, gain_db, q, sample_rate)
        self._x1l = self._x2l = self._y1l = self._y2l = 0.0
        self._x1r = self._x2r = self._y1r = self._y2r = 0.0
        # Pre-allocated work buffers — reused across process() calls to reduce allocation churn
        self._ff_buf = np.empty(480, dtype=np.float32)
        self._out_buf = np.empty(480, dtype=np.float32)

    def _set_params(self, freq: float, gain_db: float, q: float, sr: int):
        w0 = 2.0 * np.pi * freq / sr
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        A = 10.0 ** (gain_db / 40.0)
        alpha = sin_w0 / (2.0 * max(q, 0.1))

        b0 = 1.0 + alpha * A
        b1 = -2.0 * cos_w0
        b2 = 1.0 - alpha * A
        a0 = 1.0 + alpha / A
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha / A

        self._b0 = np.float32(b0/a0)
        self._b1 = np.float32(b1/a0)
        self._b2 = np.float32(b2/a0)
        self._a1 = np.float32(a1/a0)
        self._a2 = np.float32(a2/a0)

    def set_params(self, freq: float, gain_db: float, q: float):
        self._set_params(freq, gain_db, q, SAMPLE_RATE)
        # Don't reset state — allows smooth parameter changes

    def _process_channel(self, block: np.ndarray, state: tuple) -> tuple:
        """Process a 1D block with given state. Returns (output, new_state)."""
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        x1, x2, y1, y2 = state
        blen = len(block)

        # Ensure pre-allocated buffers are large enough (normally always 480)
        if blen > len(self._ff_buf):
            self._ff_buf = np.empty(blen, dtype=np.float32)
            self._out_buf = np.empty(blen, dtype=np.float32)

        # Vectorized feed-forward: b0*x[n] + b1*x[n-1] + b2*x[n-2]
        # Write into pre-allocated buffer instead of creating new array
        ff = self._ff_buf[:blen]
        np.multiply(block, b0, out=ff)
        ff[1:] += b1 * block[:-1]
        if blen > 1:
            ff[2:] += b2 * block[:-2]
        # Fix first two samples with saved state
        ff[0] += b1 * x1 + b2 * x2
        if blen > 1:
            ff[1] += b2 * x1

        # Recursive feedback (must be sequential — IIR)
        out = self._out_buf[:blen]
        for i in range(blen):
            y = ff[i] - a1 * y1 - a2 * y2
            out[i] = y
            y2 = y1
            y1 = y

        return out, (block[-1] if len(block) > 0 else x1,
                     block[-2] if len(block) > 1 else x1,
                     float(out[-1]) if len(out) > 0 else y1,
                     float(out[-2]) if len(out) > 1 else y2)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process audio block. Handles 1D (mono) or 2D (N, 2) stereo."""
        if block.ndim == 1:
            out, (self._x1l, self._x2l, self._y1l, self._y2l) = \
                self._process_channel(block, (self._x1l, self._x2l, self._y1l, self._y2l))
            return out

        # Stereo path
        out_l, (self._x1l, self._x2l, self._y1l, self._y2l) = \
            self._process_channel(block[:, 0], (self._x1l, self._x2l, self._y1l, self._y2l))
        out_r, (self._x1r, self._x2r, self._y1r, self._y2r) = \
            self._process_channel(block[:, 1], (self._x1r, self._x2r, self._y1r, self._y2r))
        return np.stack([out_l, out_r], axis=1)


# ─── EQ (8-band) ───────────────────────────────────────────────────

class EQProcessor:
    """8-band parametric EQ using cascaded biquad peaking filters."""

    DEFAULT_BANDS = [
        (60,    0.0, 1.0),
        (120,   0.0, 1.0),
        (250,   0.0, 1.0),
        (500,   0.0, 1.0),
        (1000,  0.0, 1.0),
        (2000,  0.0, 1.0),
        (4000,  0.0, 1.0),
        (8000,  0.0, 1.0),
    ]

    def __init__(self, bands: list[dict] = None, enabled: bool = True):
        self.enabled = enabled
        bands = bands or []
        self._filters: list[BiquadPeak] = []

        # Create 8 biquad filters
        for i in range(8):
            if i < len(bands):
                b = bands[i]
                freq = b.get('freq', self.DEFAULT_BANDS[i][0])
                gain = b.get('gain', 0.0)
                q = b.get('q', 1.0)
            else:
                freq, gain, q = self.DEFAULT_BANDS[i]
            self._filters.append(BiquadPeak(freq, gain, q))

    def set_band(self, idx: int, freq: float, gain_db: float, q: float):
        """Update a single band — no restart needed, instant update."""
        if 0 <= idx < len(self._filters):
            self._filters[idx].set_params(freq, gain_db, q)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return block
        for f in self._filters:
            block = f.process(block)
        return block


# ─── Compressor ────────────────────────────────────────────────────

class CompressorProcessor:
    """Feed-forward compressor with attack/release."""

    def __init__(self, threshold_db: float = -20.0, ratio: float = 3.0,
                 attack_ms: float = 3.0, release_ms: float = 250.0,
                 makeup_db: float = 0.0, enabled: bool = True):
        self.threshold = _db_to_linear(threshold_db)
        self.ratio = ratio
        self.makeup = _db_to_linear(makeup_db)
        self.enabled = enabled
        # Coefficients are applied per-BLOCK (480 samples), not per-sample.
        # Without the block-size factor, the envelope rises ~480x slower than
        # intended, causing volume to slowly decay during continuous speech.
        BLOCK = 480  # BUFFER_SAMPLES in native_chain.py
        self._attack_coef = np.exp(-BLOCK / (max(attack_ms, 0.1) * 0.001 * SAMPLE_RATE))
        self._release_coef = np.exp(-BLOCK / (max(release_ms, 0.1) * 0.001 * SAMPLE_RATE))
        self._envelope = 0.0
        self._gain = 1.0
        # Pre-allocated output buffer — reused to avoid per-call allocation
        self._out_buf = np.empty(480, dtype=np.float32)

    def set_params(self, threshold_db: float = None, ratio: float = None,
                   attack_ms: float = None, release_ms: float = None,
                   makeup_db: float = None, enabled: bool = None):
        if threshold_db is not None:
            self.threshold = _db_to_linear(threshold_db)
        if ratio is not None:
            self.ratio = ratio
        if makeup_db is not None:
            self.makeup = _db_to_linear(makeup_db)
        if attack_ms is not None:
            BLOCK = 480
            self._attack_coef = np.exp(-BLOCK / (max(attack_ms, 0.1) * 0.001 * SAMPLE_RATE))
        if release_ms is not None:
            BLOCK = 480
            self._release_coef = np.exp(-BLOCK / (max(release_ms, 0.1) * 0.001 * SAMPLE_RATE))
        if enabled is not None:
            self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return block

        # Peak envelope follower (works for 1D and 2D)
        peak = float(np.max(np.abs(block)))
        if peak > self._envelope:
            self._envelope = self._attack_coef * self._envelope + (1 - self._attack_coef) * peak
        else:
            self._envelope = self._release_coef * self._envelope + (1 - self._release_coef) * peak

        # Compression curve
        if self._envelope > self.threshold:
            env_db = _linear_to_db(self._envelope)
            thresh_db = _linear_to_db(self.threshold)
            gain_reduction_db = (env_db - thresh_db) * (1.0 - 1.0 / self.ratio)
            self._gain = _db_to_linear(-gain_reduction_db) * self.makeup
        else:
            self._gain = self.makeup

        # NaN guard
        if not np.isfinite(self._gain):
            self._gain = 1.0

        blen = len(block)
        # Handle stereo (N, 2) vs mono (N,)
        if block.ndim == 2:
            if self._out_buf.ndim != 2 or blen > self._out_buf.shape[0]:
                self._out_buf = np.empty((blen, 2), dtype=np.float32)
            np.multiply(block, np.float32(self._gain), out=self._out_buf[:blen])
        else:
            if self._out_buf.ndim != 1 or blen > len(self._out_buf):
                self._out_buf = np.empty(blen, dtype=np.float32)
            np.multiply(block, np.float32(self._gain), out=self._out_buf[:blen])
        return self._out_buf[:blen]


# ─── Limiter ───────────────────────────────────────────────────────

class LimiterProcessor:
    """Peak limiter with fast attack / program release and a hard ceiling.

    Sits at the end of the mic chain so compressor makeup gain and EQ boosts
    can't clip the processed node. Feed-forward peak detector: gain only ever
    pulls the signal *down* to the ceiling.
    """

    def __init__(self, ceiling_db: float = -1.0, attack_ms: float = 1.0,
                 release_ms: float = 80.0, enabled: bool = True):
        self.enabled = enabled
        self._ceiling_db = ceiling_db
        self.ceiling = _db_to_linear(ceiling_db)
        BLOCK = 480
        self._attack_coef = np.exp(-BLOCK / (max(attack_ms, 0.05) * 0.001 * SAMPLE_RATE))
        self._release_coef = np.exp(-BLOCK / (max(release_ms, 0.1) * 0.001 * SAMPLE_RATE))
        self._gain = 1.0

    def set_params(self, ceiling_db: float = None, attack_ms: float = None,
                   release_ms: float = None, enabled: bool = None):
        if ceiling_db is not None:
            self._ceiling_db = ceiling_db
            self.ceiling = _db_to_linear(ceiling_db)
        if attack_ms is not None:
            BLOCK = 480
            self._attack_coef = np.exp(-BLOCK / (max(attack_ms, 0.05) * 0.001 * SAMPLE_RATE))
        if release_ms is not None:
            BLOCK = 480
            self._release_coef = np.exp(-BLOCK / (max(release_ms, 0.1) * 0.001 * SAMPLE_RATE))
        if enabled is not None:
            self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return block
        peak = float(np.max(np.abs(block)))
        target = (self.ceiling / peak) if peak > self.ceiling else 1.0
        # attack when clamping harder, release when letting go
        if target < self._gain:
            self._gain = self._attack_coef * self._gain + (1 - self._attack_coef) * target
        else:
            self._gain = self._release_coef * self._gain + (1 - self._release_coef) * target
        if not np.isfinite(self._gain):
            self._gain = 1.0
        return block * np.float32(self._gain)


# ─── Adaptive Ambient Noise Reduction (FFT spectral subtraction) ─────

class AmbientNoiseReduction:
    """Adaptive spectral-subtraction NR for steady room tone.

    Estimates a slow-moving per-bin noise magnitude floor and subtracts an
    over-subtracted fraction of it (Wiener-like gain), preserving phase. This
    complements the AI denoiser by cleaning up steady ambience (fans, AC,
    hiss) that the model may leave behind. Uses a per-bin noise-floor tracker
    with over-subtraction.

    Uses its own 1024-pt STFT with a 480-sample hop (= one chain block) and
    weighted overlap-add, so it presents the same 480-in / 480-out interface.
    """

    N = 1024
    HOP = 480
    BINS = N // 2 + 1

    def __init__(self, level: float = 0.5, over_factor: float = 1.6,
                 floor_down: float = 0.20, floor_up: float = 0.003,
                 gain_smooth: float = 0.5, enabled: bool = False):
        self.enabled = enabled
        self._level = float(np.clip(level, 0.0, 1.0))       # 0 = off, 1 = full subtraction
        self._over = float(over_factor)
        self._down = float(floor_down)
        self._up = float(floor_up)
        self._gsm = float(np.clip(gain_smooth, 0.0, 0.99))
        self._win = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(self.N) / self.N)).astype(np.float32)
        self._inwin = np.zeros(self.N, dtype=np.float32)
        self._ola = np.zeros(self.N, dtype=np.float64)
        self._wsum = np.zeros(self.N, dtype=np.float64)
        self._floor = None
        self._gain_s = np.ones(self.BINS, dtype=np.float32)

    def set_params(self, level: float = None, over_factor: float = None,
                   enabled: bool = None):
        if level is not None:
            self._level = float(np.clip(level, 0.0, 1.0))
        if over_factor is not None:
            self._over = float(over_factor)
        if enabled is not None:
            self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled or self._level <= 0.001:
            return block
        if block.ndim > 1:
            block = block[:, 0]
        if len(block) != self.HOP:
            return block

        self._inwin[:-self.HOP] = self._inwin[self.HOP:]
        self._inwin[-self.HOP:] = block
        X = np.fft.rfft(self._inwin * self._win)
        mag = np.abs(X)

        if self._floor is None:
            self._floor = mag.copy()
        else:
            # fast-down / slow-up floor tracker
            self._floor = np.where(
                mag < self._floor,
                (1.0 - self._down) * self._floor + self._down * mag,
                (1.0 - self._up) * self._floor + self._up * mag,
            )

        # spectral-subtraction gain, then temporal smoothing to limit musical noise
        sub = np.maximum(mag - self._over * self._floor, 0.0) / np.maximum(mag, 1e-9)
        g = (1.0 - self._level) + self._level * sub
        self._gain_s = self._gsm * self._gain_s + (1.0 - self._gsm) * g

        Y = X * self._gain_s
        frame = np.fft.irfft(Y, n=self.N)
        self._ola += frame * self._win
        self._wsum += self._win * self._win
        out = self._ola[:self.HOP] / np.maximum(self._wsum[:self.HOP], 1e-8)
        self._ola[:-self.HOP] = self._ola[self.HOP:]; self._ola[-self.HOP:] = 0.0
        self._wsum[:-self.HOP] = self._wsum[self.HOP:]; self._wsum[-self.HOP:] = 0.0
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


# ─── Multiband Compressor (Linkwitz-Riley 4th-order crossovers) ─────

class BiquadLowPass:
    """Second-order Butterworth low-pass biquad (Direct Form I)."""

    def __init__(self, freq: float, q: float = 0.707, sample_rate: int = SAMPLE_RATE):
        self._set_params(freq, q, sample_rate)
        self._x1 = self._x2 = self._y1 = self._y2 = 0.0

    def _set_params(self, freq: float, q: float, sr: int):
        w0 = 2.0 * np.pi * freq / sr
        cw = np.cos(w0); sw = np.sin(w0)
        alpha = sw / (2.0 * max(q, 0.1))
        b0 = (1 - cw) / 2; b1 = 1 - cw; b2 = (1 - cw) / 2
        a0 = 1 + alpha; a1 = -2 * cw; a2 = 1 - alpha
        self._b0 = np.float32(b0 / a0); self._b1 = np.float32(b1 / a0); self._b2 = np.float32(b2 / a0)
        self._a1 = np.float32(a1 / a0); self._a2 = np.float32(a2 / a0)

    def process(self, block: np.ndarray) -> np.ndarray:
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        x1, x2, y1, y2 = self._x1, self._x2, self._y1, self._y2
        ff = b0 * block.copy()
        ff[1:] += b1 * block[:-1]
        if len(block) > 1:
            ff[2:] += b2 * block[:-2]
        ff[0] += b1 * x1 + b2 * x2
        if len(block) > 1:
            ff[1] += b2 * x1
        out = np.empty_like(block)
        for i in range(len(block)):
            y = ff[i] - a1 * y1 - a2 * y2
            out[i] = y
            y2 = y1; y1 = y
        self._x1 = block[-1] if len(block) else x1
        self._x2 = block[-2] if len(block) > 1 else x1
        self._y1 = float(out[-1]) if len(out) else y1
        self._y2 = float(out[-2]) if len(out) > 1 else y2
        return out


class _LinkwitzRiley4:
    """4th-order Linkwitz-Riley crossover (two cascaded 2nd-order stages)."""

    def __init__(self, freq: float, sample_rate: int = SAMPLE_RATE):
        self._lp1 = BiquadLowPass(freq, 0.707, sample_rate)
        self._lp2 = BiquadLowPass(freq, 0.707, sample_rate)
        self._hp1 = BiquadHighPass(freq, 0.707, sample_rate)
        self._hp2 = BiquadHighPass(freq, 0.707, sample_rate)

    def split(self, x: np.ndarray):
        low = self._lp2.process(self._lp1.process(x))
        high = self._hp2.process(self._hp1.process(x))
        return low, high


class MultibandCompressor:
    """N-band compressor using Linkwitz-Riley 4th-order crossovers.

    Bands are split, each compressed independently, then summed — so loud
    low-end (plosives/boom) is tamed without pumping the whole signal, and
    sibilance is controlled without dulling the body.
    """

    def __init__(self, crossovers: list[float] = None, enabled: bool = False,
                 band_params: list[dict] = None):
        self.enabled = enabled
        crossovers = crossovers or [200.0, 800.0, 3200.0]   # 4 bands
        self._x = [_LinkwitzRiley4(f) for f in crossovers]
        n_bands = len(crossovers) + 1
        self._comps: list[CompressorProcessor] = []
        for i in range(n_bands):
            p = (band_params[i] if band_params and i < len(band_params) else {})
            self._comps.append(CompressorProcessor(
                threshold_db=p.get("threshold", -18.0),
                ratio=p.get("ratio", 2.5),
                attack_ms=p.get("attack", 5.0),
                release_ms=p.get("release", 150.0),
                makeup_db=p.get("makeup", 0.0),
                enabled=True,
            ))

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def set_band(self, idx: int, **kwargs):
        if 0 <= idx < len(self._comps):
            self._comps[idx].set_params(
                threshold_db=kwargs.get("threshold"),
                ratio=kwargs.get("ratio"),
                attack_ms=kwargs.get("attack"),
                release_ms=kwargs.get("release"),
                makeup_db=kwargs.get("makeup"),
            )

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return block
        bands = []
        remaining = block
        for x in self._x:
            low, remaining = x.split(remaining)
            bands.append(low)
        bands.append(remaining)
        out = None
        for comp, band in zip(self._comps, bands):
            y = comp.process(band)
            out = y if out is None else out + y
        return out

