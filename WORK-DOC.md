# PulseForge — Work Document

**Session date:** 2026-09-17  
**Repo:** https://github.com/rdv21/pulseforge  
**Version:** 1.2.0  
**Author:** Denis Ruzaev (rdv21)

---

## What Was Done This Session

### 1. Project Reorganization & Versioning

- **Reorganized project structure** — removed redundant directories (`pulseforge_v2/`, `pulseforge-backup-2026-08-03/`, empty `data/` and `appmixer/`)
- **Semantic versioning system** — `CHANGELOG.md` (Keep a Changelog format), `VERSION` file, version in `pyproject.toml`
- **Git tags** — `v1.0.0` (initial release), `v1.1.0` (gate/ramping/fader sync), `v1.2.0` (AFX merge + reorg)
- **Docs folder** — moved `VIDEO-SCRIPT.md` and old `CHANGES-2026-08-03.md` into `docs/`
- **Cleaned `.gitignore`** — removed patterns for things that no longer exist

**Commits:** `1582766`

### 2. Merged v2 (NVIDIA AFX) Into v1

- **One codebase** — `pulseforge_v2/` directory eliminated; AFX is now a runtime feature in the main `pulseforge/` package
- **Runtime detection** — AFXNoiseProcessor tries to init on startup; if no RTX GPU or SDK, falls back gracefully
- **Fixed v2 bug** — `_GPU_ARCH_MAP` was referenced but never defined in the original v2 code; added the full mapping (sm_75/86/89/120)
- **Unified launcher** — `pulseforge.sh` auto-detects AFX SDK and sets `LD_LIBRARY_PATH` only when needed
- **5 AFX effect modes** — denoiser, denoiser_v2 (BNR 2.0), dereverb, dereverb_denoiser, studio_voice_low_latency
- **GPU arch auto-detection** — via `nvidia-smi` compute capability query, maps to model directory

**Commits:** `d2ac65b`

### 3. Removed Old Noise Processors

- **Deleted** `SpeexNoiseProcessor`, `RNNoiseVadGate`, `RNNoiseProcessor` classes from `dsp.py` (~410 lines removed)
- **Removed fallback logic** from `native_chain.py` — AFX is the only noise processor now
- **Removed** `setNoiseIntensity` / `setNoiseEnabled` slots from `main.py`
- **Removed** `noise` config section from `config.py`
- **Net: -605 lines** (991 removed, 386 added)

**Commits:** `e8287f3`

### 4. DSP Bug Fixes

- **Stereo/mono crash** — `GateProcessor`, `CompressorProcessor`, and `RNNoiseVadGate` all used 1D pre-allocated buffers but received 2D `(480, 2)` stereo arrays
- **Fix:** proper broadcasting (`gc[:, np.newaxis]` for gate), ndim tracking + reallocation for compressor and RNNoise
- All processors now handle both `(480,)` mono and `(480, 2)` stereo inputs

**Commits:** `395ef95`

### 5. UI Cleanup (Mic Settings)

- **Removed old "Noise Cancellation" card** — AFX is the only noise control
- **Gate + Compressor side by side** — equal height via `Layout.fillHeight` + `minimumHeight` binding
- **Dynamic window height** — `Math.min(860, Screen.desktopAvailableHeight * 0.9)` adapts to screen
- **ScrollView** wraps content to prevent overflow on smaller screens
- **Compact layout** — reduced EQ height (400→280px), tighter spacing, smaller fonts
- **AFX section always visible** — shows "No RTX GPU detected" in red if unavailable
- **Removed debug `console.log` spam**

**Commits:** `e8287f3`, `2fbfef5`

### 6. Desktop File Fixes

- **Autostart** (`~/.config/autostart/pulseforge.desktop`) — was pointing to deleted `pulseforge-v2.sh`
- **App launcher** (`~/.local/share/applications/pulseforge.desktop`) — same fix
- **Removed** `pulseforge-v2.desktop` (redundant)
- **Set** `PrefersNonDefaultGPU=true` for RTX GPU usage

### 7. Published to GitHub

- **Repo created:** https://github.com/rdv21/pulseforge
- All 12 commits pushed to `main`
- Tags `v1.0.0`, `v1.1.0`, `v1.2.0` pushed
- Public repo, MIT license

### 8. Soundboard — 3×3 Grid + Recording + Waveform Editor

**Backend** (`pulseforge/backend/soundboard.py`):
- `SoundboardBackend` — 3 pages × 3×3 grid (27 slots), JSON config persistence
- `ChannelRecorder` — 15-second ring buffer per channel, captures from PipeWire virtual sinks via `pw-cat --record`
- `AudioClip` — stores captured samples, trim region, WAV export (float32 → int16)
- Clip management — capture, get info, set trim, play preview (temp WAV + pw-play), stop, clear, discard
- Waveform peaks — 200 data points for QML Canvas rendering

**Main bridge** (`pulseforge/main.py`):
- Soundboard slots: `getSoundboardSlots`, `assignSound`, `clearSoundSlot`, `playSound`, `openSoundFileDialog`
- Recording slots: `startChannelRecording`, `stopChannelRecording`, `isChannelRecording`, `getRecordingChannels`
- Clip slots: `captureClip`, `getClipInfo`, `getClipWaveform`, `setClipTrim`, `playClipPreview`, `stopClipPreview`, `isClipPlaying`, `clearClip`, `exportClip`

**QML** (`pulseforge/qml/SoundboardWindow.qml`):
- Two-view layout: Grid view ↔ Clip Editor (toggle button)
- Grid view: 3 page tabs, 3×3 buttons, click-to-play, long-press for file dialog
- Recording section: 5 channel buttons (Game/Chat/Media/Aux/Mic) with live REC indicator
- Live waveform Canvas (100ms refresh) showing real-time audio
- Clip editor: waveform with green/dimmed trim regions, orange draggable handles, trim sliders, playback controls
- Playback: Play Trimmed (exports temp WAV, pw-play), Stop, Reset Trim, Export WAV, Discard

**Main.qml** — soundboard button between separator and app routing panel

**Commits:** `31e6764`, `ddcd3b8`, `db4c90b`

---

## What Still Needs Work

### Soundboard — Needs Testing on Real Hardware

- [ ] **End-to-end recording test** — ChannelRecorder uses `pw-cat --record --target=<sink> --format=f32 -o -`; this has been unit-tested but not tested with actual PipeWire audio flowing
- [ ] **AFX + recording simultaneously** — verify AFX noise removal and channel recording work together
- [ ] **pw-cat stdout capture** — confirm `pw-cat` can pipe raw float32 to stdout reliably (may need `--rate` and `--channels` flags validated)
- [ ] **Sound file playback** — `pw-play` for slot sounds and clip preview both need real-world testing
- [ ] **QML Canvas performance** — waveform Canvas redraw at 100ms intervals may need optimization for smoothness

### Soundboard — UI Polish

- [ ] **Sound slot volume control** — per-slot volume slider
- [ ] **Stop-all-sounds button** — emergency stop
- [ ] **Visual playback indicator** — pulsing/animation on slots while playing
- [ ] **Right-click to clear slot** — alternative to long-press menu
- [ ] **Slot empty state** — better visual for unassigned slots (subtle "+" icon, maybe drag-and-drop support)
- [ ] **Keyboard shortcuts** — number keys 1-9 to trigger sounds on current page
- [ ] **Page name editing** — let user rename "Page 1/2/3" to custom names

### Soundboard — Backend Improvements

- [ ] **Audio format validation** — `pw-play` supports wav/mp3/ogg/flac but some may need conversion
- [ ] **Clip storage** — currently clips are ephemeral (lost on restart); consider saving to `~/.config/pulseforge/soundboard/clips/`
- [ ] **Recording buffer size** — 15s is hardcoded; consider making configurable
- [ ] **Multi-channel recording** — record multiple channels simultaneously (currently single-channel toggle)
- [ ] **Clip metadata** — store channel, timestamp, and trim settings with exported WAVs

### Core PulseForge — Known Issues

- [ ] **System install is stale** — `/usr/lib/python3.14/site-packages/pulseforge/` has the August 1st version; user site-packages (`~/.local/lib/python3.14/site-packages/`) is current. Consider `sudo make install` to update system install, or remove system install entirely
- [ ] **No pip** — system doesn't have pip installed; `make install` is the only install path (requires sudo)
- [ ] **AFX status=6** — on the current machine (no RTX GPU or SDK), AFX returns error code 6 on CreateEffect; this is expected behavior but the error message could be cleaner
- [ ] **Main.qml WindowStaysOnTopHint** — main window stays on top; may want to make this configurable

### Future Features (Noted, Not Started)

- [ ] **Channel mixing** — mix between channels in the soundboard (e.g., blend game + mic, route a sound to a specific channel). This was explicitly requested as a future feature
- [ ] **MIDI binding** — trigger soundboard slots via MIDI controller
- [ ] **Per-slot loop mode** — loop sounds instead of one-shot
- [ ] **Recorded clip library** — save and organize clips with metadata
- [ ] **Crossfade between clips** — smooth transitions when switching between recorded clips

---

## Architecture Overview

```
pulseforge/
├── backend/
│   ├── config.py          — config load/save, default config, first-run auto-detect
│   ├── dsp.py             — GateProcessor, EQProcessor, CompressorProcessor, AFXNoiseProcessor
│   ├── native_chain.py    — mic processing chain: AFX → gate → EQ → compressor → pw-cat
│   ├── pipewire_ctl.py    — PipeWire/WirePlumber control (sinks, volumes, loopbacks, routing)
│   ├── process_manager.py — pw-cat process tracking and orphan cleanup
│   ├── soundboard.py      — soundboard grid, channel recording, clip management, waveform
│   ├── vu_meter.py        — VU meter polling via pactl
│   └── app_router.py      — app-to-channel routing
├── qml/
│   ├── Main.qml           — main mixer window (channel strips, app routing, status bar)
│   ├── MicSettingsWindow.qml — mic processing UI (AFX, gate, EQ, compressor)
│   ├── SoundboardWindow.qml  — soundboard + recording + waveform editor
│   └── components/        — reusable QML components (ChannelStrip, Fader, VUMeter, etc.)
├── main.py               — PulseForgeBridge (QML ↔ Python), app entry point
├── launch.py             — alternative launcher
└── pulseforge.sh          — shell launcher (AFX-aware)
```

### Audio Flow
```
Apps → Virtual Sinks (game/chat/media/aux) → Gaming (master mix) → Hardware Output
                                                    ↓
                                          Stream Sink → Virtual Source (for OBS/Discord)

Hardware Mic → pw-cat --record → AFX (noise removal) → Gate → EQ → Compressor → pw-cat --playback → pulseforge.mic.processed
```

### Soundboard Audio Flow
```
Virtual Sink → pw-cat --record --target=<sink> → Ring Buffer (15s) → Capture Clip → Waveform Editor → Trim → Export WAV
                                                                                                    → Play Preview (pw-play)
```

---

## Quick Reference

| Item | Value |
|---|---|
| GitHub | https://github.com/rdv21/pulseforge |
| Version | 1.2.0 |
| Python | 3.14 |
| Qt | PySide6 (Qt 6) |
| Audio | PipeWire 1.0+ / WirePlumber |
| Noise | NVIDIA AFX (RTX required) |
| Config | `~/.config/pulseforge/` |
| Soundboard config | `~/.config/pulseforge/soundboard/soundboard.json` |
| Install (user) | `~/.local/lib/python3.14/site-packages/pulseforge/` |
| Install (system, stale) | `/usr/lib/python3.14/site-packages/pulseforge/` |
| Launcher | `pulseforge.sh` or `/usr/bin/pulseforge` |
