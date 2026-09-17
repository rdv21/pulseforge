# PulseForge Video Script

## Part 1: What Is PulseForge?

**Hook:** Linux audio routing is a mess. If you've ever spent an hour fighting pactl commands, JACK bridges, or PulseAudio configs just to get OBS and Discord sounding right — this is for you.

**What it is:**
PulseForge is a virtual audio mixer for Linux. It runs on PipeWire and gives you a full mixing desk — channel strips, faders, VU meters, mic processing, stream routing — all in one app. No terminal, no config files, no JACK.

**Who it's for:**
Streamers, gamers, podcasters, and anyone on Linux who wants clean audio without becoming an audio engineer. If you come from Windows and used Voicemeeter or SteelSeries GG Sonar — this is the Linux equivalent.

**The problem it solves:**
On Linux, your audio goes through PipeWire → WirePlumber → ALSA. There's no built-in mixer UI. Want to lower just your game volume without lowering Discord? You're opening a separate volume app or running pactl commands. Want noise cancellation on your mic? You're setting up LV2 filter-chains. Want a separate stream mix for OBS? Good luck. PulseForge does all of this in one window.

**Key philosophy:** It manages its own audio graph. You don't need to configure PipeWire or WirePlumber — PulseForge creates its own virtual sinks, sources, and routing. It cleans up after itself on exit. It auto-detects your hardware on first run. It just works.

---

## Part 2: Functionality & How To Use It

### Getting Started

1. Install and launch — PulseForge auto-detects your output device and microphone on first run. No setup wizard, no config file editing.
2. The main window shows a mixing desk: channel strips for Game, Chat, Media, Aux, and Mic, plus a Master fader.

### The Mixer — Channel Strips

Each channel strip (Game, Chat, Media, Aux) has:

- **Volume Fader (right side):** Controls how loud this channel is in your main mix (what you hear). Drag up/down, double-click to reset to 100%, scroll wheel for fine control.
- **Stream Fader (left side):** Controls how loud this channel is in your **stream mix** — what OBS and Discord capture. This is independent from your main volume. You can have game audio loud for yourself but quiet for your stream.
- **STREAM Button:** Toggles whether this channel goes to the stream mix at all. Green = streaming, off = not in stream mix.
- **MUTE Button:** Mutes the channel globally — both your speakers and the stream.
- **VU Meters:** Two meters per strip. The right (blue) shows your main mix level. The left (green) shows your stream mix level — what OBS actually captures, post-stream-fader.
- **App Label:** Shows which apps are currently routed to this channel.

### App Routing Panel

Below the mixer, there's a grid showing every running audio app. Each app has a dropdown to assign it to a channel: Game, Chat, Media, or Aux.

- New apps default to Aux — you route them where you want
- Changes are instant — no restart, no delay
- Assignments persist between sessions

### Device Selectors

At the top of the window:

- **Output:** Where your main mix plays (your speakers/headphones)
- **Input:** Which microphone PulseForge processes
- **Stream:** Where the stream mix goes (usually your default output, but can be different)

Switching any device reconnects automatically — no restart needed.

### Mic Settings Window

Click the gear icon to open the mic processing window. This is where PulseForge replaces SteelSeries GG Sonar / Voicemeeter:

**Monitor Button:** Hear yourself through your speakers in real-time. Useful for testing your mic chain.

**8-Band Parametric EQ:**
- Visual frequency response curve — drag the dots to adjust
- X-axis: frequency (20Hz–20kHz, log scale)
- Y-axis: gain (−6dB to +6dB)
- Scroll wheel on a dot: adjust Q (bandwidth)
- Real-time FFT spectrum overlay shows your mic input overlaid on the EQ curve
- **Presets:** Save and load EQ presets. Comes with "Deep Voice" and "Broadcast Clean" presets to get you started.

**Noise Cancellation:**
- Intensity slider 0–100%
- Uses RNNoise voice activity detection as a smart gate
- Suppresses keyboard clicks, table taps, fan noise, room tone
- Gentle curve so mumbled/quiet speech still comes through
- No robotic artifacts — it modulates gain based on voice probability, not wet/dry mixing

**Noise Gate:**
- Threshold slider
- RMS-based detection (more natural than peak detection)
- Smooth attack/release — no chopping
- Fully closes to zero — no signal leaks when you're not talking

**Compressor:**
- Threshold and ratio sliders
- Feed-forward design with block-correct envelope follower
- Transparent defaults for clean voice — doesn't sound "squashed"
- Makeup gain control

**All parameters update in real-time.** Move a slider and you hear the change instantly. No process restarts, no audio gaps.

### Stream Mix

PulseForge creates a virtual microphone called "PulseForge Stream Input" that OBS, Discord, and other apps can select as their mic. This captures your stream mix — whatever combination of channels you've enabled with the STREAM button, plus your processed mic if you enable it.

The stream mix is completely separate from what you hear. No echo loops, no double audio.

### Fader Sync

If you change volume outside PulseForge (pactl, hardware knob, desktop volume widget), the faders in PulseForge update to match within 2 seconds. Won't fight you if you're actively dragging a fader.

### Status Bar

Bottom of the window shows status messages. Blue for info ("PulseForge ready"), red for errors ("Mic processing chain failed to start"). Auto-hides after 4 seconds.

---

## Part 3: Technical Details

### Architecture

PulseForge is a single-process application:

- **Frontend:** QML / Qt Quick — GPU-accelerated, matches KDE Plasma theme
- **Backend:** Python — PipeWire control via pactl/pw-cli, native DSP mic chain

**Why QML?** It's what KDE Plasma uses. GPU-accelerated, declarative, renders smoothly at 60fps. The dark theme integrates with your desktop.

### Audio Graph

```
Apps → [Game/Chat/Media/Aux virtual sinks] → Gaming (master mix) → Hardware output
                  ↓                              ↑
           Stream mix sink ← (stream-enabled channels)
                  ↓
           PulseForge Stream Input (virtual microphone → OBS/Discord)
                  
Mic → [pw-cat capture → DSP chain → pw-cat playback] → Mic Source → apps
```

PulseForge creates 6 virtual PipeWire sinks (game, chat, media, aux, gaming, stream) and routes audio between them using module-loopback with volume ramping to prevent pops/clicks.

### Mic Processing Chain

The mic chain is the interesting part. Instead of using PipeWire's filter-chain (which requires process restarts to change parameters), PulseForge runs its own DSP pipeline in Python:

1. **Capture:** `pw-cat --record` captures raw float32 audio from the hardware mic (480 samples = 10ms blocks)
2. **RNNoise VAD Gate:** Loads librnnoise via ctypes. RNNoise processes each frame and outputs a voice activity probability (0.0–1.0). This probability is used as a smooth gain multiplier — `output = input × vad_prob^scale`. Keyboard clicks get probability ~0.1 (suppressed), speech gets ~0.9 (passes through). The intensity slider controls the exponent curve. A +24dB pre-gain is applied internally so RNNoise can detect voice on quiet dynamic mics, then the gain is applied to the original signal.
3. **Noise Gate:** RMS-based with hysteresis and hold timer. Smooth linear ramp between open/closed states. Fully closes to zero.
4. **8-Band Parametric EQ:** Cascaded biquad peaking filters (Direct Form I). Vectorized feed-forward, sequential feedback (IIR can't be vectorized). Real-time parameter updates with no state reset.
5. **Compressor:** Feed-forward with block-correct envelope follower. Attack/release coefficients account for block size (480 samples) to prevent the slow-decay bug.
6. **Playback:** `pw-cat --playback` creates the `pulseforge.mic.processed` virtual source. A WAV header is written first so libsndfile can parse the stream format.

**Non-blocking writes:** The playback write uses non-blocking I/O. If PipeWire's buffer fills during a CPU spike, the block is dropped (10ms, imperceptible) instead of stalling the entire chain.

**Anti-suspend:** The capture node has `session.suspend-timeout-seconds=0` to prevent PipeWire from auto-suspending it during silence. A watchdog detects extended silence and explicitly resumes the hardware source via `pactl suspend-source`.

### Latency

- Buffer size: 480 samples (10ms at 48kHz)
- `PIPEWIRE_LATENCY=480/48000` environment variable forces small buffers
- `--latency 480` flag on pw-cat for double enforcement
- Total chain latency: ~20-30ms (capture buffer + processing + playback buffer)
- Non-blocking playback writes prevent chain stalls

### Volume Ramping

All loopback connections use volume ramping:
- **Connect:** Start at 0% volume, ramp to target over 150ms
- **Disconnect:** Ramp to 0% over 100ms, then unload module
- Eliminates the pop/click that occurs from instant signal start/stop

### Dependencies

- PipeWire 1.0+ (tested on 1.6.8)
- WirePlumber (session manager — PulseForge works within it, not against it)
- Python 3.11+, PySide6 (Qt 6), numpy
- librnnoise (noise suppression — VAD gate)
- libspeexdsp (installed but not currently used — available for future)

### What It Does NOT Touch

- No global PipeWire config changes
- No WirePlumber script modifications
- No `/etc/` edits
- All changes are runtime, per-node properties
- Cleans up all virtual sinks/sources/loopbacks on exit

---

## Part 4: Future Features

### Soundboard with Widget Control

A built-in soundboard — load audio files (sounds, alerts, intro music), trigger them with hotkeys or a widget. The soundboard outputs through a dedicated channel in the mixer, so you can control its volume independently for your main mix and stream mix. A desktop widget (plasmoid or floating panel) gives you one-click access to trigger sounds without opening the main window.

Use cases: stream alerts, podcast stingers, game sound effects, transition music. All routed through PulseForge's mixing system so your viewers hear it at the right level.

### High Sample Rate Processing

Currently the entire chain runs at 48kHz. Future support for 96kHz and 192kHz processing for higher fidelity. This matters for:
- Music production use cases
- Audiophile setups with high-end DACs
- Reducing aliasing artifacts in the EQ and compressor

The DSP code (biquad coefficients, RNNoise frame size, compressor envelope) is sample-rate aware — the architecture supports it, it just needs testing and UI controls to select the rate. RNNoise would need retraining or a 96kHz model.

### Removing WirePlumber Reliance

Right now PulseForge creates virtual sinks and sources through pactl, which goes through WirePlumber. This works, but it means PulseForge is at the mercy of WirePlumber's session management — it can move streams, suspend sources, and change defaults in ways that conflict with PulseForge's routing.

The future plan: use `pw-cli` and the PipeWire C API directly to create and manage nodes, bypassing WirePlumber entirely. This gives PulseForge full control over its audio graph with no middleman. Benefits:
- No more source suspension issues (WirePlumber won't suspend what it doesn't manage)
- No stream-restore fighting (WirePlumber's module-stream-restore can override app routing)
- Direct port links instead of module-loopback (lower latency, native volume control on links)
- More predictable behavior across distros (different WirePlumber configs won't break things)

This is a significant refactor — PipeWire's C API via ctypes or a small C extension module — but it eliminates the entire class of "WirePlumber did something unexpected" bugs.

### Other Planned Features

- **System tray improvements:** Per-channel mute from tray, quick device switching
- **AUR package:** `yay -S pulseforge` for Arch/CachyOS users
- **Profile system:** Save and load entire mixer states (volumes, routing, mic settings) as profiles
- **MIDI control:** Map faders and buttons to a MIDI controller for physical mixing
- **Theme support:** Light theme, custom accent colors
- **Multi-output:** Send main mix to multiple hardware outputs simultaneously (speakers + headphones)

---

## Outro

PulseForge is open source, MIT licensed, on GitHub. It's built by a Linux user who got tired of audio being hard. If you want to help test, file issues, or contribute — links in the description.

Linux audio doesn't have to suck. We just need the right tools.
