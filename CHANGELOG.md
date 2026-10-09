# Changelog

All notable changes to PulseForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **AI Denoise (Sonar DeepVQE-S)** — the SteelSeries Sonar noise-cancellation neural
  model (Microsoft DeepVQE-S, ONNX) runs natively in the mic chain as an optional
  denoiser, independent of NVIDIA AFX and usable on any GPU/CPU. Includes a
  **Strength** dry/wet slider in both the Qt mic window and the web panel
  (`/api/mic/deepvqe/status|enabled|strength`). Offline validation on real speech:
  ~9 dB SNR improvement, ~0.75 ms/frame on CPU. Toggle + strength persist to config
  (`mic.deepvqe`). The ONNX graph was reverse-engineered from the Sonar audio driver
  (`Sonar.APO.dll`) — see `docs/deepvqe.md`.
- **Aux In selector** — pick a second external input device in the header and it is looped
  straight into the Aux channel (module-loopback → `pulseforge_aux`), so any line-in/interface
  can join the mix. Persisted as `devices.aux_input`, restored on startup, never stacks
  loopbacks, and `None` clears the route
- **Web control panel** — browser/VR UI served on `http://localhost:8765` alongside the Qt app
  (mixer, mic, and soundboard control; real-time VU over WebSocket; stdlib-only server)
- **Collision-safe soundboard publishing** — publishing a clip whose name already exists no
  longer overwrites it; an in-app dialog offers rename (auto-suggested `Name (1)`) or explicit overwrite

### Fixed
- **Soundboard no longer lags / bloats memory while open** — channel recorders now keep a fixed
  raw `float32` ring buffer instead of a `deque` of boxed Python floats. The 10 Hz live-waveform
  refresh copied that deque into NumPy on the Qt GUI thread each tick (~20 ms per call, ~0.3 s CPU
  per 10 s), stalling repaints and input; it also churned the glibc heap into hundreds of arenas
  that never returned to the OS (multi-GB RSS + swap after long sessions). Snapshot + peak/RMS
  computation is now vectorized and the waveform call drops to ~7 ms
- `pulseforge.sh` caps `MALLOC_ARENA_MAX=2` to stop per-thread arena growth
- Reap finished `pw-play` children so soundboard playback no longer accumulates zombie processes
- Clean up transient `_preview.wav` / `_publish.wav` scratch files on shutdown
- `pulseforge.sh` now runs the repository package (single source of truth) instead of
  silently importing the installed copy from site-packages

### Changed
- Makefile and `pyproject.toml` now install/ship the `pulseforge.web` panel assets
- README: corrected repo URL, dropped removed speex/rnnoise prerequisites, documented the
  web panel and the AFX-only mic noise chain, added ffmpeg requirement

### Removed
- Legacy `pipewire/pulseforge.conf` (old Calf LV2 + RNNoise filter-chain architecture, unused)

## [1.2.0] - 2026-09-17

### Added
- **NVIDIA AFX integration** — AI-powered noise removal on RTX GPUs
  - Supports 5 effect modes: denoiser, denoiser_v2 (BNR 2.0), dereverb, dereverb_denoiser, studio_voice_low_latency
  - Automatic GPU architecture detection via nvidia-smi (sm_75/86/89/120)
  - CUDA/TensorRT preload with RTLD_GLOBAL for proper symbol resolution
  - Runtime detection with automatic fallback to speexdsp on non-RTX systems
  - AFX UI section in Mic Settings: mode selector, intensity slider, enable toggle
- AFX configuration section in config.json (`afx.enabled`, `afx.effect_mode`, `afx.intensity`, `afx.sdk_root`)
- Unified launcher script auto-detects AFX SDK and sets LD_LIBRARY_PATH

### Changed
- Merged separate pulseforge_v2/ directory into main codebase — one codebase, runtime feature detection
- Updated launcher (pulseforge.sh) to handle both AFX and non-AFX environments
- Updated README with AFX documentation

### Removed
- pulseforge_v2/ directory (merged into pulseforge/)
- pulseforge-v2.sh (unified into pulseforge.sh)
- pulseforge-backup-2026-08-03/ (redundant with git history)
- Empty directories (data/, appmixer/)

## [1.1.0] - 2026-09-17

### Added
- **Gate improvements**: adjustable range floor (-25dB default), attack/hold/release parameters, pre-allocated numpy buffers for performance
- **Volume ramping**: loopback connect/disconnect now ramps volume over 150ms to prevent popping
- **Fader sync**: poll-based (every 2s) reads pactl sink-inputs and syncs UI faders with external volume changes; respects user drag
- **Error surfacing**: status toast bar in Main.qml, error checks at sink creation and mic chain startup
- **First-run auto-detect**: config.py auto-detects output (prefers alsa_output) and input (prefers USB mics) devices on first run

### Changed
- Stream VU now shows pre-stream-fader signal (what's available to stream, not post-fader)
- Gate no longer hardcodes range to 0.0 (complete cutoff); uses -25dB for natural breath ambience
- Process cleanup uses ProcessManager.cleanup_orphans() instead of pkill
- ChannelStrip: added `channelName` property for backend fader matching

### Fixed
- Gate popping on open/close (smooth attack/release ramps)
- Stream VU showing incorrect levels (was post-stream-fader, now pre)
- Fader desync between UI and pactl when external apps change volume

## [1.0.0] - 2026-08-03

### Added
- Initial release of PulseForge
- 5 channel strips (Game, Chat, Media, Aux, Mic) + master output
- Independent volume and stream mix faders per channel
- Real-time VU meters with peak hold and clip detection
- App routing — assign running apps to channels via dropdown
- Studio-grade mic chain: speexdsp noise suppression → noise gate → 8-band parametric EQ → compressor
- Stream routing via virtual PipeWire sinks and sources
- QML dark-themed UI with channel strips, app routing grid, mic settings window
- Configuration stored in ~/.config/pulseforge/
- Makefile for system-wide install with .desktop entry
