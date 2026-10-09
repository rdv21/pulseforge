# AI Denoise (Sonar DeepVQE-S)

PulseForge can run the **SteelSeries Sonar** noise-cancellation neural network
natively as an optional mic processor, independent of NVIDIA AFX.

## What it is

The model is **Microsoft DeepVQE-S** — a streaming speech-enhancement network
(GRU + multi-scale convolutional encoder/decoder) trained by SteelSeries as
their "R-Vox" denoiser (`R-Vox_DnzDrvMultiChannel_4.0.0.onnx`). It ships inside
Sonar's Windows audio driver (`Sonar.APO.dll`) as an embedded ONNX graph and was
extracted for use here.

- Model file: `pulseforge/models/sonar_deepvqe.onnx` (2.3 MB, ~540K params)
- Runtime: `onnxruntime` (CPU by default; CUDA/DirectML if available)
- Independent of AFX — either, both, or neither may be enabled

## Signal contract (reverse-engineered)

Derived from the model graph + `Sonar.APO.dll` metadata, and validated offline:

| Stage | Setting |
|-------|---------|
| STFT | 1024-point FFT, 480-sample hop (10 ms block), periodic Hann |
| Model input | `[1, 1, seq, 513, 2]` float32 — (real, imag) per bin |
| Model output | `[1, 1, seq, 513, 2]` — enhanced complex spectrum, used directly |
| Streaming | GRU + conv memories fed back each frame (state carried across calls) |
| ISTFT | weighted overlap-add, window-power normalised, oldest hop emitted |

Notes:
- The graph carries its **own input normalisation**; the output is already in the
  STFT domain. Do **not** apply a mask or de-normalise — use it directly.
- Model output is ~0.71× input on speech; a fixed `MAKEUP = 1.4` restores unity.
- Latency: one window (1024 samples ≈ 21 ms). Fine for mic use.

## Controls

- **Enable** toggle and **Strength** slider (dry/wet blend, 0–100 %) in the Qt mic
  window and the web panel.
- API: `GET /api/mic/deepvqe/status`, `POST /api/mic/deepvqe/enabled`,
  `POST /api/mic/deepvqe/strength`.
- Persisted under `mic.deepvqe` = `{enabled, strength}`.

## Performance / validation

- ~0.75 ms/frame on CPU (Ryzen-class) → ~13× realtime headroom at a 10 ms block.
- Offline test on real speech with added noise: SNR **−0.7 dB → +7…9 dB**.

## Dependency

`onnxruntime` must be importable by the interpreter that runs PulseForge. If it
is missing, the processor reports `available: false` and is a no-op passthrough.
