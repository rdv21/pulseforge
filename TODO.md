# PulseForge TODO

## Soundboard

### Implemented
- [x] 3×3 grid with 3 pages
- [x] Long-press to open file dialog and bind sounds
- [x] Click to play assigned sound
- [x] Per-channel ring buffer recording (15s, game/chat/media/aux/mic)
- [x] Real-time waveform display
- [x] Capture clip + export to WAV

### Needs Work
- [ ] Waveform editor with draggable trim sliders (start/end markers)
- [ ] Clip playback (preview before export)
- [ ] Sound slot volume control
- [ ] Stop-all-sounds button
- [ ] Visual playback indicator (pulsing/animation while playing)
- [ ] Right-click to clear slot (alternative to long-press menu)

### Future
- [ ] **Channel mixing** — mix between channels in the soundboard
  (e.g., blend game + mic, or route a sound to a specific channel)
- [ ] Soundboard slot MIDI binding (trigger sounds via MIDI controller)
- [ ] Per-slot loop mode
- [ ] Recorded clip library (save and organize clips)
- [ ] Crossfade between recorded clips
