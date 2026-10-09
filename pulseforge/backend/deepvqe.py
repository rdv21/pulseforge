"""DeepVQE-S AI speech denoiser (ONNX).

Runs the DeepVQE noise-cancellation model (Microsoft DeepVQE-S,
"R-Vox_DnzDrvMultiChannel_4.0.0") as a native Python mic processor.

Signal contract (reverse-engineered from the Windows audio driver + validated offline):

  * STFT: 1024-point FFT, 480-sample hop (= one native 10 ms block), periodic
    Hann analysis/synthesis window.
  * Model input : ``[1, 1, seq, 513, 2]`` float32 — (real, imag) per bin.
  * Model output: ``[1, 1, seq, 513, 2]`` — the enhanced complex spectrum in
    the same STFT domain (the graph carries its own input normalisation; the
    output is used directly — no mask, no de-normalisation).
  * Streaming: the model keeps recurrent state (GRU + conv memories); each
    call's state outputs are fed back as the next call's inputs.
  * ISTFT: weighted overlap-add (WOLA), window-power normalised; the *oldest*
    480-sample hop of the accumulator is emitted (time-aligned output).

Offline validation on real speech: noisy SNR -0.7 dB → +8.5 dB (~9 dB
suppression), ~0.75 ms/frame on CPU (~13x realtime headroom at 10 ms/block).

Strength: dry/wet blend in the STFT domain (0.0 = bypass, 1.0 = full model).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

# ─── Audio / STFT constants (must match the model) ─────────────────
SAMPLE_RATE = 48000
N_FFT = 1024
HOP = 480                      # == native chain block size (10 ms)
BINS = N_FFT // 2 + 1          # 513
MAKEUP = 1.4                   # model output ~0.71x input on speech; restore unity

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "deepvqe.onnx"


def _periodic_hann(n: int) -> np.ndarray:
    """Periodic (DFT-even) Hann window — what the model was trained with."""
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)).astype(np.float32)


_W = _periodic_hann(N_FFT)


class DeepVQEDenoiser:
    """Streaming AI speech denoiser backed by an ONNX DeepVQE-S model.

    Thread-affinity: ``process()`` runs on the single mic processing thread.
    Parameter setters are plain attribute writes and are safe from the UI
    thread.
    """

    def __init__(self, model_path: Optional[str] = None, strength: float = 0.7,
                 enabled: bool = True):
        self._model_path = str(model_path or _MODEL_PATH)
        self._strength = float(np.clip(strength, 0.0, 1.0))
        self.enabled = bool(enabled)

        self._session = None
        self._state: dict = {}
        self._state_names: list = []
        self._state_shapes: dict = {}
        self._lock = threading.Lock()
        self._error: Optional[str] = None

        # Streaming buffers
        self._inwin = np.zeros(N_FFT, dtype=np.float32)      # sliding input window
        self._ola = np.zeros(N_FFT, dtype=np.float64)        # overlap-add accumulator
        self._wsum = np.zeros(N_FFT, dtype=np.float64)       # window-power normaliser

        self._load()

    # ─── lifecycle ────────────────────────────────────────────────

    def _load(self):
        try:
            import onnxruntime as ort  # noqa: local import — optional dependency
        except Exception as e:  # pragma: no cover - env dependent
            self._error = f"onnxruntime unavailable: {e}"
            return
        if not Path(self._model_path).exists():
            self._error = f"model not found: {self._model_path}"
            return
        try:
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            avail = ort.get_available_providers()
            providers = [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider")
                         if p in avail] or ["CPUExecutionProvider"]
            self._session = ort.InferenceSession(
                self._model_path, sess_options=opts, providers=providers
            )
            ins = self._session.get_inputs()
            self._state_names = [i.name for i in ins if i.name != "input"]
            self._state_shapes = {
                i.name: [1 if (d in ("audioChannel", "sequenceSize")) else
                         (d if isinstance(d, int) else 1) for d in i.shape]
                for i in ins if i.name != "input"
            }
            self._reset_state()
            print(f"  DeepVQE: loaded ({Path(self._model_path).name}), "
                  f"provider={self._session.get_providers()[0]}")
        except Exception as e:  # pragma: no cover
            self._error = f"model load failed: {e}"
            self._session = None

    def _reset_state(self):
        if self._session is None:
            return
        self._state = {
            name: np.zeros(self._state_shapes[name], dtype=np.float32)
            for name in self._state_names
        }
        self._inwin[:] = 0.0
        self._ola[:] = 0.0
        self._wsum[:] = 0.0

    def reset(self):
        with self._lock:
            self._reset_state()

    # ─── parameters ───────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._session is not None

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def strength(self) -> float:
        return self._strength

    def set_strength(self, strength: float):
        self._strength = float(np.clip(strength, 0.0, 1.0))

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    # ─── processing ───────────────────────────────────────────────

    def process(self, block: np.ndarray) -> np.ndarray:
        """Denoise one 480-sample block; returns 480 samples.

        Falls back to passthrough when disabled, unavailable, or given an
        unexpected block size.
        """
        if not self.enabled or self._session is None:
            return block
        if block.ndim > 1:
            block = block[:, 0]
        if len(block) != HOP:
            return block

        with self._lock:
            # slide the input window: drop oldest hop, append the new block
            self._inwin[:-HOP] = self._inwin[HOP:]
            self._inwin[-HOP:] = block

            spec = np.fft.rfft(self._inwin * _W)                 # [513]
            inp = np.stack([spec.real, spec.imag], axis=-1).astype(np.float32)
            inp = inp[None, None, ...]                           # [1,1,513,2]

            feed = dict(self._state)
            feed["input"] = inp
            try:
                outs = self._session.run(None, feed)
            except Exception as e:  # pragma: no cover - runtime guard
                print(f"  DeepVQE: inference error: {e}")
                return block
            wet = np.asarray(outs[0]).reshape(BINS, 2)
            wet = wet[:, 0] + 1j * wet[:, 1]
            self._state = {name: v for name, v in zip(self._state_names, outs[1:])}

            s = self._strength
            if s >= 0.999:
                blended = wet * MAKEUP
            elif s <= 0.001:
                blended = spec
            else:
                blended = (1.0 - s) * spec + s * (wet * MAKEUP)

            frame = np.fft.irfft(blended, n=N_FFT)               # [1024]
            self._ola += frame * _W
            self._wsum += _W * _W
            # emit the OLDEST hop (time-aligned, fully accumulated)
            out = self._ola[:HOP] / np.maximum(self._wsum[:HOP], 1e-8)
            self._ola[:-HOP] = self._ola[HOP:]; self._ola[-HOP:] = 0.0
            self._wsum[:-HOP] = self._wsum[HOP:]; self._wsum[-HOP:] = 0.0

        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def destroy(self):
        with self._lock:
            self._session = None
            self._state = {}
