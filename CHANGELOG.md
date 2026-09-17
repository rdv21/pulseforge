# Changelog

All notable changes to PulseForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
