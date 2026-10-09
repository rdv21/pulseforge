# PulseForge TODO

## Soundboard

### Implemented
- [x] 3×3 grid with 3 pages
- [x] Long-press to open file dialog and bind sounds
- [x] Click to play assigned sound
- [x] Per-channel ring buffer recording (15s, game/chat/media/aux/mic)
- [x] Real-time live waveform display
- [x] Capture clip from ring buffer
- [x] Waveform editor with draggable trim handles (start/end markers)
- [x] Clip playback (preview trimmed audio via pw-play)
- [x] Collision-safe publish — in-app rename/overwrite dialog instead of silent overwrite
- [x] Web control panel (browser/VR) — soundboard, mixer, mic over HTTP+WebSocket
- [x] Two-view layout (Grid ↔ Clip Editor toggle)

### Needs Testing
- [ ] End-to-end recording with actual PipeWire audio
- [ ] pw-cat stdout capture reliability (float32 pipe)
- [ ] Mic + recording simultaneously
- [ ] Sound file playback via pw-play
- [ ] QML Canvas performance at 100ms refresh

### Needs Work
- [ ] Sound slot volume control (per-slot slider)
- [ ] Stop-all-sounds button
- [ ] Visual playback indicator (pulsing/animation while playing)
- [ ] Right-click to clear slot
- [ ] Keyboard shortcuts (1-9 to trigger sounds)
- [ ] Page name editing (rename "Page 1/2/3")
- [ ] Clip persistence (save clips to disk, survive restart)
- [ ] Configurable recording buffer size (currently hardcoded 15s)

### Future
- [ ] **Channel mixing** — mix between channels in the soundboard
  (e.g., blend game + mic, or route a sound to a specific channel)
- [ ] Soundboard slot MIDI binding (trigger sounds via MIDI controller)
- [ ] Per-slot loop mode
- [ ] Recorded clip library (save and organize clips)
- [ ] Crossfade between recorded clips

## Core PulseForge

### Mic Processing
- [x] AI denoise (DeepVQE ONNX model) with strength slider — top-level control
- [x] Chain reorder: denoise → NR → gate (gate runs post-NR)
- [x] Adaptive ambient NR (FFT spectral subtraction, room tone) — Advanced tab
- [x] High-pass filter (rumble/plosives, ~90 Hz) — Advanced tab
- [x] Multiband compressor (4-band Linkwitz-Riley crossovers) — Advanced tab
- [x] Output limiter (-1 dB ceiling) — Advanced tab
- [x] Auto-threshold noise gate (tracks noise floor + offset) — Advanced tab
- [x] Removed NVIDIA AFX support entirely (superseded by the ONNX AI denoise)
- [ ] **AEC (Acoustic Echo Cancellation)** — cancel speaker output from the mic so
  callers don't hear your game/media. Needs a reference signal per render bus
  (loopback each group sink and feed as the AEC reference). Largest remaining gap
  vs a full mic suite; separate project.
- [ ] Impact/click noise reduction (keyboard/desk transients)
- [ ] De-esser / clarity (sibilance)
- [ ] EQ filter types: add shelf + HP/LP, extend to 10 bands
- [ ] Run DeepVQE on its native 512-hop / seq-2 framing (~1-2 dB more, fewer artifacts)

### Needs Work
- [ ] Stale system install (`/usr/lib/python3.14/site-packages/pulseforge/`, `/usr/bin/pulseforge`,
  system `.desktop` + icons) — **deferred: needs sudo**. Repo is now the source of truth, so this
  is no longer on the runtime path; remove with `sudo make uninstall` when convenient.
- [ ] Main.qml WindowStaysOnTopHint — make configurable
- [ ] Old user install `~/.local/lib/python3.14/site-packages/pulseforge/` is now unused
  (launcher imports the repo); remove once confirmed
