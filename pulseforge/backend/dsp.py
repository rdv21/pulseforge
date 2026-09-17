"""DSP processors for native Python mic processing.

Implemented from scratch with numpy — no scipy dependency.

Processors:
  - Gate: threshold-based expander with attack/release
  - EQ: 8-band peaking biquad filter
  - RNNoise: loaded via ctypes from librnnoise
  - NVIDIA AFX: AI-powered noise removal via NVIDIA Audio Effects SDK (ctypes)
  - Compressor: feed-forward compressor with attack/release

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
                 range_db: float = -25.0):
        # RMS-based detection: speech RMS is ~12dB below peak, so lower
        # the RMS threshold by 12dB to match the same perceptual opening point.
        self._rms_offset_db = -12.0
        self.threshold = _db_to_linear(threshold_db + self._rms_offset_db)
        self._gate_open = False
        self.enabled = enabled
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
        self.threshold = _db_to_linear(threshold_db + self._rms_offset_db)

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
        if blen > len(self._out_buf):
            self._out_buf = np.empty(blen, dtype=np.float32)
        np.multiply(block, np.float32(self._gain), out=self._out_buf[:blen])
        return self._out_buf[:blen]


# ─── Noise Suppression (speexdsp via ctypes) ────────────────────────

class SpeexNoiseProcessor:
    """Speexdsp noise suppression via ctypes.

    Uses the speex preprocessor API which provides continuous, natural-sounding
    noise suppression with a configurable suppression level (dB attenuation).
    Much smoother than RNNoise's binary suppress/don't-suppress behavior.

    API: speex_preprocess_state_init(frame_size, sampling_rate)
          speex_preprocess_run(state, samples)  # in-place, int16
          speex_preprocess_ctl(state, SPEEX_PREPROCESS_SET_DENOISE, &enabled)
          speex_preprocess_ctl(state, SPEEX_PREPROCESS_SET_NOISE_SUPPRESS, &db)
    """

    FRAME_SIZE = 480  # 10ms at 48kHz — must match native_chain BUFFER_SAMPLES

    # speex_preprocess_ctl constants
    _SET_DENOISE = 0
    _GET_DENOISE = 1
    _SET_VAD = 4
    _SET_NOISE_SUPPRESS = 18
    _GET_NOISE_SUPPRESS = 19

    def __init__(self, intensity: float = 50.0, enabled: bool = True):
        """Initialize speex noise suppression.

        Args:
            intensity: 0-100, maps to suppression depth in dB.
                       0 = off, 100 = -60dB suppression, 50 = -30dB.
            enabled: Whether processing is active.
        """
        self.enabled = enabled
        self._intensity = intensity

        self._lib = None
        self._state = None
        self._in_buf = None  # int16 ctypes array

        self._init_lib()

    def _init_lib(self):
        """Load libspeexdsp via ctypes."""
        try:
            lib_path = ctypes.util.find_library("speexdsp")
            if lib_path is None:
                for p in ["/usr/lib/libspeexdsp.so", "/usr/lib/libspeexdsp.so.1"]:
                    if Path(p).exists():
                        lib_path = p
                        break

            if lib_path is None:
                print("  SpeexNS: library not found")
                return

            self._lib = ctypes.CDLL(lib_path)

            # speex_preprocess_state_init(int frame_size, int sampling_rate) → SpeexPreprocessState*
            self._lib.speex_preprocess_state_init.restype = ctypes.c_void_p
            self._lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]

            # speex_preprocess_run(SpeexPreprocessState*, spx_int16_t* x) → int
            self._lib.speex_preprocess_run.restype = ctypes.c_int
            self._lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]

            # speex_preprocess_ctl(state, int request, void* ptr) → int
            self._lib.speex_preprocess_ctl.restype = ctypes.c_int
            self._lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

            # speex_preprocess_state_destroy(state)
            self._lib.speex_preprocess_state_destroy.restype = None
            self._lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]

            # Create preprocessor state (480 samples, 48kHz)
            self._state = self._lib.speex_preprocess_state_init(self.FRAME_SIZE, SAMPLE_RATE)
            if not self._state:
                print("  SpeexNS: state init failed")
                self._lib = None
                return

            # Enable denoise (noise suppression)
            denoise_on = ctypes.c_int(1)
            self._lib.speex_preprocess_ctl(self._state, self._SET_DENOISE, ctypes.byref(denoise_on))

            # Disable VAD — our gate handles that
            vad_off = ctypes.c_int(0)
            self._lib.speex_preprocess_ctl(self._state, self._SET_VAD, ctypes.byref(vad_off))

            # Set initial suppression level
            self._apply_suppression()

            # Pre-allocate int16 buffer for in-place processing
            self._in_buf = (ctypes.c_int16 * self.FRAME_SIZE)()

            print(f"  SpeexNS: initialized (frame_size={self.FRAME_SIZE}, intensity={self._intensity})")

        except Exception as e:
            print(f"  SpeexNS: init failed: {e}")
            self._lib = None

    def _apply_suppression(self):
        """Apply current intensity as dB suppression level."""
        if not self._lib or not self._state:
            return
        # Map 0-100 intensity to 0 to -60 dB suppression
        # 0 = no suppression, 100 = -60dB (max)
        suppress_db = ctypes.c_int(int(-(self._intensity / 100.0) * 60.0))
        self._lib.speex_preprocess_ctl(self._state, self._SET_NOISE_SUPPRESS, ctypes.byref(suppress_db))

    def set_intensity(self, intensity: float):
        """Set suppression intensity (0-100).

        0 = no suppression, 100 = maximum (-60dB attenuation).
        Updates in real-time — no restart needed.
        """
        self._intensity = max(0.0, min(100.0, intensity))
        self._apply_suppression()

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process audio block. Handles 1D (mono) or 2D (N, 2) stereo.

        Speex works on int16 samples in-place. We convert float32 → int16,
        process, then convert back.
        """
        if not self.enabled or self._lib is None or not self._state:
            return block

        if len(block) != self.FRAME_SIZE:
            return block

        if block.ndim == 1:
            # Mono path
            # float32 (-1.0..1.0) → int16 (-32768..32767)
            scaled = np.clip(block * 32767.0, -32768, 32767).astype(np.int16)
            self._in_buf[:] = scaled
            self._lib.speex_preprocess_run(self._state, self._in_buf)
            out = np.asarray(self._in_buf, dtype=np.int16).astype(np.float32) / 32767.0
            return out.astype(np.float32)
        else:
            # Stereo — process each channel independently
            out = np.empty_like(block)
            for ch in range(block.shape[1]):
                scaled = np.clip(block[:, ch] * 32767.0, -32768, 32767).astype(np.int16)
                self._in_buf[:] = scaled
                self._lib.speex_preprocess_run(self._state, self._in_buf)
                out[:, ch] = np.asarray(self._in_buf, dtype=np.int16).astype(np.float32) / 32767.0
            return out

    def destroy(self):
        """Clean up speex state."""
        if self._lib and self._state:
            self._lib.speex_preprocess_state_destroy(self._state)
            self._state = None


# ─── RNNoise VAD Gate (transient suppression) ──────────────────────

class RNNoiseVadGate:
    """RNNoise-based transient noise gate.

    Uses RNNoise's VAD (voice activity detection) probability as a smooth
    gain multiplier: output = input * vad_prob^scale

    This is much better than wet/dry mix for transient sounds:
    - Keyboard clicks, table taps → low VAD probability → suppressed
    - Speech → high VAD probability → passes through
    - No robotic artifacts (no wet signal mixing, just gain)

    The `intensity` scales how aggressively the VAD probability is applied:
    - 0% = no gating (pass through)
    - 100% = full VAD gate (output = input * vad_prob)
    """

    FRAME_SIZE = 480

    def __init__(self, intensity: float = 50.0, enabled: bool = True):
        self.enabled = enabled
        self._intensity = intensity

        self._lib = None
        self._state = None
        self._in_buf = None
        self._out_buf = None

        # Smoothing: exponential moving average of VAD prob for natural transitions
        self._vad_smooth = 0.0
        self._smoothing = 0.06  # lower = smoother, keeps gate open through brief dips

        # Pre-gain: RNNoise needs signal around -20 to 0dB to detect voice.
        # Dynamic mics sit at -40 to -60dB, so boost before VAD detection,
        # then restore original level after.
        self._pre_gain = 16.0   # +24dB before RNNoise
        self._post_atten = 0.0625  # -24dB after (restore original level)

        # Pre-allocated output buffer for gain application
        self._gain_buf = np.empty(self.FRAME_SIZE, dtype=np.float32)

        self._init_lib()

    def _init_lib(self):
        try:
            lib_path = ctypes.util.find_library("rnnoise")
            if lib_path is None:
                for p in ["/usr/lib/librnnoise.so", "/usr/lib/librnnoise.so.0"]:
                    if Path(p).exists():
                        lib_path = p
                        break
            if lib_path is None:
                print("  RNNoiseVadGate: library not found")
                return

            self._lib = ctypes.CDLL(lib_path)
            self._lib.rnnoise_get_frame_size.restype = ctypes.c_int
            self._lib.rnnoise_get_frame_size.argtypes = []
            frame_size = self._lib.rnnoise_get_frame_size()
            if frame_size != self.FRAME_SIZE:
                self.FRAME_SIZE = frame_size

            self._lib.rnnoise_create.restype = ctypes.c_void_p
            self._lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            self._lib.rnnoise_process_frame.restype = ctypes.c_float
            self._lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)
            ]
            self._lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]

            self._state = self._lib.rnnoise_create(None)
            self._in_buf = (ctypes.c_float * self.FRAME_SIZE)()
            self._out_buf = (ctypes.c_float * self.FRAME_SIZE)()
            self._in_np = np.ctypeslib.as_array(self._in_buf)
            self._out_np = np.ctypeslib.as_array(self._out_buf)
            print(f"  RNNoiseVadGate: initialized (frame_size={self.FRAME_SIZE})")
        except Exception as e:
            print(f"  RNNoiseVadGate: init failed: {e}")
            self._lib = None

    def set_intensity(self, intensity: float):
        self._intensity = max(0.0, min(100.0, intensity))

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled or self._lib is None or not self._state:
            return block
        if len(block) != self.FRAME_SIZE:
            return block

        block = np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0)

        # Pre-gain so RNNoise can detect voice on quiet dynamic mics
        vad_in = block * np.float32(self._pre_gain)
        if not np.isfinite(vad_in).all():
            vad_in = np.zeros_like(vad_in)

        if block.ndim == 1:
            self._in_np[:] = vad_in
            vad_prob = self._lib.rnnoise_process_frame(
                self._state, self._out_buf, self._in_buf
            )
        else:
            # Stereo — average VAD across channels
            vad_probs = []
            for ch in range(vad_in.shape[1]):
                self._in_np[:] = vad_in[:, ch]
                vp = self._lib.rnnoise_process_frame(
                    self._state, self._out_buf, self._in_buf
                )
                vad_probs.append(vp)
            vad_prob = sum(vad_probs) / len(vad_probs)

        # Smooth the VAD probability for natural transitions
        self._vad_smooth = self._smoothing * vad_prob + (1 - self._smoothing) * self._vad_smooth
        vad = max(0.0, min(1.0, self._vad_smooth))

        # Scale VAD by intensity using a gentle curve:
        # gain = vad^(0.01 + intensity/100 * 0.49)
        # At 0%: gain = vad^0.01 ≈ 1.0 (pass through)
        # At 50%: gain = vad^0.26 (gentle gating)
        # At 100%: gain = vad^0.50 (moderate — mumbled voice still audible)
        # This means:
        #   vad=0.0 (noise) → gain=0.0 (gated) at any intensity
        #   vad=0.3 (mumble) → gain=0.55 at 100% (audible, not silenced)
        #   vad=0.9 (speech) → gain=0.95 at 100% (passes through)
        exponent = 0.01 + (self._intensity / 100.0) * 0.49
        gain = max(0.0, vad) ** exponent

        if not np.isfinite(gain):
            gain = 1.0

        # Apply gain to the ORIGINAL signal (not the pre-gained version)
        # Returns view into _gain_buf — safe because processing loop consumes it before next call
        np.multiply(block, np.float32(gain), out=self._gain_buf[:len(block)])
        return self._gain_buf[:len(block)]

    def destroy(self):
        if self._lib and self._state:
            self._lib.rnnoise_destroy(self._state)
            self._state = None


# ─── Legacy RNNoise (kept for fallback) ────────────────────────────

class RNNoiseProcessor:
    """RNNoise noise suppression via ctypes shared library.

    DEPRECATED: Use SpeexNoiseProcessor instead. Kept as fallback.
    """

    FRAME_SIZE = 480

    def __init__(self, vad_threshold: float = 30.0, enabled: bool = True):
        self.enabled = enabled
        self.vad_threshold = vad_threshold / 100.0

        self._lib = None
        self._state_l = None
        self._state_r = None
        self._in_buf = None
        self._out_buf = None

        self._init_lib()

    def _init_lib(self):
        try:
            lib_path = ctypes.util.find_library("rnnoise")
            if lib_path is None:
                for p in ["/usr/lib/librnnoise.so", "/usr/lib/librnnoise.so.0"]:
                    if Path(p).exists():
                        lib_path = p
                        break
            if lib_path is None:
                print("  RNNoise: library not found")
                return

            self._lib = ctypes.CDLL(lib_path)
            self._lib.rnnoise_get_frame_size.restype = ctypes.c_int
            self._lib.rnnoise_get_frame_size.argtypes = []
            frame_size = self._lib.rnnoise_get_frame_size()
            if frame_size != self.FRAME_SIZE:
                self.FRAME_SIZE = frame_size

            self._lib.rnnoise_create.restype = ctypes.c_void_p
            self._lib.rnnoise_create.argtypes = [ctypes.c_void_p]
            self._lib.rnnoise_process_frame.restype = ctypes.c_float
            self._lib.rnnoise_process_frame.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)
            ]
            self._lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]

            self._state_l = self._lib.rnnoise_create(None)
            self._state_r = self._lib.rnnoise_create(None)
            self._in_buf = (ctypes.c_float * self.FRAME_SIZE)()
            self._out_buf = (ctypes.c_float * self.FRAME_SIZE)()
            self._in_np = np.ctypeslib.as_array(self._in_buf)
            self._out_np = np.ctypeslib.as_array(self._out_buf)
            print(f"  RNNoise: initialized (frame_size={self.FRAME_SIZE})")
        except Exception as e:
            print(f"  RNNoise: init failed: {e}")
            self._lib = None

    def set_vad_threshold(self, vad: float):
        self.vad_threshold = min(vad / 100.0, 0.95)

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.enabled or self._lib is None:
            return block
        if len(block) != self.FRAME_SIZE:
            return block

        if block.ndim == 1:
            self._in_np[:] = block
            self._lib.rnnoise_process_frame(self._state_l, self._out_buf, self._in_buf)
            out = self._out_np.copy()
        else:
            out = np.empty_like(block)
            for ch_idx, state in enumerate([self._state_l, self._state_r]):
                self._in_np[:] = block[:, ch_idx]
                self._lib.rnnoise_process_frame(state, self._out_buf, self._in_buf)
                out[:, ch_idx] = self._out_np

        wet = self.vad_threshold
        dry = 1.0 - wet
        out = out * np.float32(wet) + block * np.float32(dry)
        return out

    def destroy(self):
        if self._lib:
            if self._state_l:
                self._lib.rnnoise_destroy(self._state_l)
            if self._state_r:
                self._lib.rnnoise_destroy(self._state_r)


# ─── NVIDIA AFX Noise Processor ──────────────────────────────────

class AFXNoiseProcessor:
    """NVIDIA Audio Effects SDK (AFX) noise removal via ctypes.

    Uses NVIDIA's AI-powered denoiser running on the RTX GPU via CUDA/TensorRT.
    Dramatically superior to RNNoise — removes background noise, fans, keyboard
    clicks, and room noise while preserving voice clarity.

    Supports multiple effect modes:
      - 'denoiser': Noise removal (standard)
      - 'denoiser_v2': BNR 2.0 (improved denoiser)
      - 'dereverb': Room echo removal
      - 'dereverb_denoiser': Combined noise + room echo
      - 'studio_voice_low_latency': Studio Voice (low latency)

    Requires:
      - NVIDIA RTX GPU (RTX 20/30/40/50 series)
      - libnv_audiofx.so (NVIDIA AFX SDK)
      - CUDA + TensorRT runtime
      - Model files (.trtpkg) for the appropriate GPU architecture

    The intensity parameter (0.0-1.0) controls how aggressively noise is removed.
    AFX runs on GPU at ~10ms latency (480 samples at 48kHz).
    """

    SAMPLE_RATE = 48000
    FRAME_SIZE = 480  # 10ms at 48kHz — matches native_chain BUFFER_SAMPLES

    # NVIDIA AFX parameter names (STRING-based, not integer enums)
    _PARAM_USE_DEFAULT_GPU = b'use_default_gpu'
    _PARAM_INPUT_SAMPLE_RATE = b'input_sample_rate'
    _PARAM_NUM_STREAMS = b'num_streams'
    _PARAM_NUM_SAMPLES_PER_INPUT_FRAME = b'num_samples_per_input_frame'
    _PARAM_MODEL_PATH = b'model_path'
    _PARAM_INTENSITY_RATIO = b'intensity_ratio'
    _PARAM_ENABLE_VAD = b'enable_vad'
    _PARAM_EFFECT_VERSION = b'effect_version'

    # NvAFX_Status codes
    _NVAFX_STATUS_SUCCESS = 0

    # Compute capability → AFX model directory mapping
    _GPU_ARCH_MAP = {
        '7.5': 'sm_75',    # RTX 20 series
        '8.6': 'sm_86',    # RTX 30 series
        '8.9': 'sm_89',    # RTX 40 series
        '12.0': 'sm_120',  # RTX 50 series (Blackwell)
    }

    def __init__(self, sdk_root: str = None, effect_mode: str = 'denoiser',
                 intensity: float = 0.7, enabled: bool = True,
                 frame_samples: int = 480):
        """Initialize NVIDIA AFX noise processor.

        Args:
            sdk_root: Path to AFX SDK root directory containing nvafx/ and features/.
                      If None, tries common locations.
            effect_mode: One of 'denoiser', 'denoiser_v2', 'dereverb',
                         'dereverb_denoiser', 'studio_voice_low_latency'.
            intensity: 0.0-1.0, how aggressively to remove noise.
            enabled: Whether processing is active.
            frame_samples: Frame size (480 for 10ms, 960 for 20ms).
        """
        self.enabled = enabled
        self._intensity = max(0.0, min(1.0, intensity))
        self._effect_mode = effect_mode
        self._frame_samples = frame_samples

        self._lib = None
        self._handle = None
        self._supports_intensity = True
        self._in_buf = None
        self._out_buf = None
        self._in_np = None
        self._out_np = None

        # Determine SDK root and effect details
        self._sdk_root = sdk_root or self._find_sdk_root()
        if not self._sdk_root:
            print("  AFX: SDK root not found — falling back to RNNoise")
            return

        self._init_lib()

    def _find_sdk_root(self) -> str | None:
        """Search common locations for the AFX SDK."""
        candidates = [
            # Linux Broadcast portable bundle
            Path.home() / '.local/share/linux-broadcast/nvidia/current',
            # Environment variable
            Path(os.environ.get('AFX_SDK_ROOT', '')) if os.environ.get('AFX_SDK_ROOT') else None,
            # Common install locations
            Path('/opt/nvidia/afx'),
            Path('/usr/local/afx'),
            Path.home() / '.local/share/linux-broadcast',
        ]

        for c in candidates:
            if c and c.exists():
                # Verify expected structure
                nvfx_lib = c / 'nvafx' / 'lib' / 'libnv_audiofx.so'
                if not nvfx_lib.exists():
                    # Try alternate layout (portable bundle)
                    nvfx_lib = c / 'lib' / 'libnv_audiofx.so'
                if nvfx_lib.exists() or (c / 'nvafx' / 'lib').exists():
                    return str(c)
                # Even without the lib, if features/ exists it might work
                if (c / 'features').exists():
                    return str(c)

        return None

    def _detect_gpu_arch(self) -> str:
        """Detect GPU compute capability and map to AFX model directory."""
        try:
            r = subprocess.run(
                ['nvidia-smi', '--query-gpu=compute_cap', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0:
                cap = r.stdout.strip().split('\n')[0].strip()
                # Map compute capability to model directory
                # e.g. "8.9" → "sm_89"
                major, minor = cap.split('.')
                arch = f"sm_{major}{minor}"
                # Verify it's a known arch
                if cap in self._GPU_ARCH_MAP:
                    arch = self._GPU_ARCH_MAP[cap]
                return arch
        except Exception:
            pass

        # Fallback: try to determine from nvidia-smi GPU name
        try:
            r = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True, text=True, timeout=5
            )
            name = r.stdout.strip().lower()
            if 'rtx 40' in name or 'rtx ada' in name:
                return 'sm_89'
            elif 'rtx 30' in name or 'rtx a' in name:
                return 'sm_86'
            elif 'rtx 20' in name:
                return 'sm_75'
            elif 'rtx 50' in name or 'blackwell' in name:
                return 'sm_120'
        except Exception:
            pass

        # Default to sm_89 (RTX 40 series — most common for this setup)
        return 'sm_89'

    def _resolve_effect(self) -> dict:
        """Resolve effect mode to selector, model path, and capabilities."""
        mode = self._effect_mode
        sdk = Path(self._sdk_root)
        arch = self._detect_gpu_arch()

        effects = {
            'denoiser': {
                'selector': 'denoiser',
                'feature': 'denoiser',
                'model': 'denoiser_48k.trtpkg',
                'supports_intensity': True,
                'supports_vad': True,
                'version_2': False,
                'enable_vad': False,
            },
            'denoiser_v2': {
                'selector': 'denoiser',
                'feature': 'denoiser',
                'model': 'denoiser_v2_48k.trtpkg',
                'supports_intensity': True,
                'supports_vad': True,
                'version_2': True,
                'enable_vad': True,
            },
            'dereverb': {
                'selector': 'dereverb',
                'feature': 'dereverb',
                'model': 'dereverb_48k.trtpkg',
                'supports_intensity': False,
                'supports_vad': False,
                'version_2': False,
                'enable_vad': False,
            },
            'dereverb_denoiser': {
                'selector': 'dereverb_denoiser',
                'feature': 'dereverb_denoiser',
                'model': 'dereverb_denoiser_48k.trtpkg',
                'supports_intensity': True,
                'supports_vad': True,
                'version_2': False,
                'enable_vad': False,
            },
            'studio_voice_low_latency': {
                'selector': 'studio_voice_low_latency',
                'feature': 'studio_voice',
                'model': 'studio_voice_low_latency_48k.trtpkg',
                'supports_intensity': False,
                'supports_vad': False,
                'version_2': False,
                'enable_vad': False,
            },
        }

        if mode not in effects:
            raise ValueError(f"Unknown AFX effect mode: {mode}")

        spec = effects[mode]

        # Find model file — check multiple path patterns
        model_path = sdk / 'features' / spec['feature'] / 'models' / arch / spec['model']
        if not model_path.exists():
            # Try without arch subdir
            model_path = sdk / 'features' / spec['feature'] / 'models' / spec['model']
        if not model_path.exists():
            # Try lib directory (some bundles stage models there)
            model_path = sdk / 'features' / spec['feature'] / 'lib' / spec['model']
        if not model_path.exists():
            raise FileNotFoundError(
                f"AFX model not found: {spec['model']}\n"
                f"  Searched under: {sdk}/features/{spec['feature']}/models/{arch}/\n"
                f"  And: {sdk}/features/{spec['feature']}/models/\n"
                f"  GPU arch: {arch}"
            )

        spec['model_path'] = str(model_path)
        spec['arch'] = arch
        return spec

    def _find_lib(self) -> str | None:
        """Find libnv_audiofx.so in the SDK."""
        sdk = Path(self._sdk_root)
        candidates = [
            sdk / 'nvafx' / 'lib' / 'libnv_audiofx.so',
            sdk / 'lib' / 'libnv_audiofx.so',
            sdk / 'lib64' / 'libnv_audiofx.so',
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return None

    def _init_lib(self):
        """Load and initialize the NVIDIA AFX library via ctypes.

        Preloads CUDA/TensorRT with RTLD_GLOBAL so symbols are available
        to the feature-specific libraries (denoiser, dereverb, etc.)
        that AFX dlopens internally.
        """
        try:
            lib_path = self._find_lib()
            if not lib_path:
                print(f"  AFX: libnv_audiofx.so not found under {self._sdk_root}")
                return

            sdk = Path(self._sdk_root)
            cuda_dir = sdk / 'external' / 'cuda' / 'lib'

            # Preload CUDA/TensorRT with RTLD_GLOBAL — critical for denoiser lib
            cuda_libs = [
                'libcudart.so.12', 'libcublas.so.12', 'libcublasLt.so.12',
                'libcufft.so.11', 'libnvrtc.so.12',
                'libnvinfer.so.10', 'libnvinfer_plugin.so.10',
            ]
            for libname in cuda_libs:
                p = cuda_dir / libname
                if p.exists():
                    try:
                        ctypes.CDLL(str(p), mode=ctypes.RTLD_GLOBAL)
                    except Exception as e:
                        print(f"  AFX: preload {libname}: {e}")

            self._lib = ctypes.CDLL(lib_path)

            # --- Resolve function signatures (STRING parameter names) ---

            self._lib.NvAFX_CreateEffect.restype = ctypes.c_int
            self._lib.NvAFX_CreateEffect.argtypes = [
                ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)
            ]

            self._lib.NvAFX_SetU32.restype = ctypes.c_int
            self._lib.NvAFX_SetU32.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]

            self._lib.NvAFX_SetFloat.restype = ctypes.c_int
            self._lib.NvAFX_SetFloat.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_float]

            self._lib.NvAFX_SetStringList.restype = ctypes.c_int
            self._lib.NvAFX_SetStringList.argtypes = [
                ctypes.c_void_p, ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_char_p), ctypes.c_uint
            ]

            self._lib.NvAFX_Load.restype = ctypes.c_int
            self._lib.NvAFX_Load.argtypes = [ctypes.c_void_p]

            self._lib.NvAFX_Run.restype = ctypes.c_int
            self._lib.NvAFX_Run.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
                ctypes.c_uint, ctypes.c_uint
            ]

            self._lib.NvAFX_DestroyEffect.restype = None
            self._lib.NvAFX_DestroyEffect.argtypes = [ctypes.c_void_p]

            # --- Create and configure the effect ---
            spec = self._resolve_effect()
            self._supports_intensity = spec['supports_intensity']

            handle = ctypes.c_void_p()
            selector = spec['selector'].encode('utf-8')
            status = self._lib.NvAFX_CreateEffect(selector, ctypes.byref(handle))
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: CreateEffect failed (status={status}) for '{spec['selector']}'")
                self._lib = None
                return
            self._handle = handle

            # Use default GPU (device 0)
            status = self._lib.NvAFX_SetU32(
                self._handle, self._PARAM_USE_DEFAULT_GPU, 0)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: SetGPU failed (status={status})")

            # Set sample rate to 48kHz
            status = self._lib.NvAFX_SetU32(
                self._handle, self._PARAM_INPUT_SAMPLE_RATE, self.SAMPLE_RATE)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: SetSampleRate failed (status={status})")

            # Single stream
            status = self._lib.NvAFX_SetU32(
                self._handle, self._PARAM_NUM_STREAMS, 1)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: SetNumStreams failed (status={status})")

            # Frame size
            status = self._lib.NvAFX_SetU32(
                self._handle, self._PARAM_NUM_SAMPLES_PER_INPUT_FRAME,
                self._frame_samples)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: SetFrameSize failed (status={status})")

            # BNR 2.0 version flag
            if spec.get('version_2'):
                status = self._lib.NvAFX_SetU32(
                    self._handle, self._PARAM_EFFECT_VERSION, 2)
                if status != self._NVAFX_STATUS_SUCCESS:
                    print(f"  AFX: SetEffectVersion failed (status={status})")

            # Enable VAD if supported
            if spec.get('enable_vad'):
                status = self._lib.NvAFX_SetU32(
                    self._handle, self._PARAM_ENABLE_VAD, 1)
                if status != self._NVAFX_STATUS_SUCCESS:
                    print(f"  AFX: EnableVAD failed (status={status})")

            # Set model path
            model_path_bytes = spec['model_path'].encode('utf-8')
            models = (ctypes.c_char_p * 1)(model_path_bytes)
            status = self._lib.NvAFX_SetStringList(
                self._handle, self._PARAM_MODEL_PATH, models, 1)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: SetModelPath failed (status={status})")
                self._cleanup_handle()
                return

            # Load the model (compiles TensorRT engines — may take a few seconds)
            print(f"  AFX: Loading model '{spec['model']}' (arch={spec.get('arch', '?')}, "
                  f"mode={self._effect_mode})...")
            status = self._lib.NvAFX_Load(self._handle)
            if status != self._NVAFX_STATUS_SUCCESS:
                print(f"  AFX: Load failed (status={status})")
                self._cleanup_handle()
                return

            # Set initial intensity
            if self._supports_intensity:
                status = self._lib.NvAFX_SetFloat(
                    self._handle, self._PARAM_INTENSITY_RATIO,
                    ctypes.c_float(self._intensity))
                if status != self._NVAFX_STATUS_SUCCESS:
                    print(f"  AFX: SetIntensity failed (status={status})")

            # Pre-allocate buffers
            self._in_buf = (ctypes.c_float * self._frame_samples)()
            self._out_buf = (ctypes.c_float * self._frame_samples)()
            self._in_np = np.ctypeslib.as_array(self._in_buf)
            self._out_np = np.ctypeslib.as_array(self._out_buf)

            print(f"  AFX: initialized successfully "
                  f"(mode={self._effect_mode}, intensity={self._intensity}, "
                  f"frame={self._frame_samples})")

        except Exception as e:
            print(f"  AFX: init failed: {e}")
            self._cleanup_handle()
            self._lib = None

    def _cleanup_handle(self):
        """Destroy the AFX handle if it exists."""
        if self._lib and self._handle:
            try:
                self._lib.NvAFX_DestroyEffect(self._handle)
            except Exception:
                pass
            self._handle = None

    def set_intensity(self, intensity: float):
        """Set noise removal intensity (0.0-1.0)."""
        self._intensity = max(0.0, min(1.0, intensity))
        if self._lib and self._handle and self._supports_intensity:
            self._lib.NvAFX_SetFloat(
                self._handle, self._PARAM_INTENSITY_RATIO,
                ctypes.c_float(self._intensity)
            )

    def set_enabled(self, enabled: bool):
        self.enabled = enabled

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process audio block through NVIDIA AFX."""
        if not self.enabled or self._lib is None or not self._handle:
            return block

        if len(block) != self._frame_samples:
            return block

        # Squeeze stereo to mono (AFX processes mono)
        if block.ndim == 2:
            mono = block.mean(axis=1)
        else:
            mono = block

        # Copy input to ctypes buffer
        np.copyto(self._in_np, mono.astype(np.float32))

        # Prepare float** arrays (1 stream)
        in_ptr = ctypes.cast(self._in_buf, ctypes.POINTER(ctypes.c_float))
        out_ptr = ctypes.cast(self._out_buf, ctypes.POINTER(ctypes.c_float))
        in_arr = (ctypes.POINTER(ctypes.c_float) * 1)(in_ptr)
        out_arr = (ctypes.POINTER(ctypes.c_float) * 1)(out_ptr)

        status = self._lib.NvAFX_Run(
            self._handle,
            in_arr, out_arr,
            self._frame_samples, 1
        )

        if status != self._NVAFX_STATUS_SUCCESS:
            return block

        result = self._out_np.copy()

        # If input was stereo, duplicate mono output to both channels
        if block.ndim == 2:
            result = np.stack([result, result], axis=1)

        return result.astype(np.float32)

    def destroy(self):
        """Clean up AFX resources."""
        self._cleanup_handle()
        self._lib = None

    @property
    def available(self) -> bool:
        """True if AFX was successfully initialized."""
        return self._lib is not None and self._handle is not None

    @property
    def effect_mode(self) -> str:
        return self._effect_mode

    def set_effect_mode(self, mode: str):
        """Change effect mode — requires re-initialization."""
        if mode == self._effect_mode:
            return
        self._effect_mode = mode
        self.destroy()
        self._init_lib()
