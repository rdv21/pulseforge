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
- [x] Export trimmed clip to WAV
- [x] Two-view layout (Grid ↔ Clip Editor toggle)

### Needs Testing
- [ ] End-to-end recording with actual PipeWire audio
- [ ] pw-cat stdout capture reliability (float32 pipe)
- [ ] AFX + recording simultaneously
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

### Needs Work
- [ ] Update or remove stale system install (`/usr/lib/python3.14/site-packages/pulseforge/`)
- [ ] Cleaner AFX "not available" messaging (currently prints error code 6)
- [ ] Main.qml WindowStaysOnTopHint — make configurable
