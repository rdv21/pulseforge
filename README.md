# 🎛️ PulseForge

**A virtual audio mixer for Linux that just works.**

Route apps to channels, process your mic with studio-grade DSP, and control your entire audio setup from one clean interface — no JACK, no PulseAudio bridges, no terminal gymnastics.

Built for PipeWire. Runs on any modern Linux desktop.

---

## What it does

Most Linux audio tools are built for audio engineers. PulseForge is built for people who just want their audio to sound good — streamers, gamers, podcasters, and anyone tired of wrestling with `pactl` commands.

### 🎚️ Full Mixer Control

- **5 channel strips** (Game, Chat, Media, Aux, Mic) + master output
- Independent **volume** and **stream mix** faders per channel
- Real-time **VU meters** with peak hold and clip detection
- One-click **mute** and **stream routing** per channel
- **App routing** — see every running app, assign it to a channel from a dropdown

### 🎤 Mic Processing

Studio-grade mic chain running in real-time, all parameters adjustable with zero latency:

- **NVIDIA AFX** — AI-powered noise removal on RTX GPUs (denoiser, dereverb, studio voice). Automatically detected at runtime; the only noise processor (AFX is required for noise removal)
- **Noise gate** — RMS-based detection with smooth attack/release, adjustable range floor
- **8-band parametric EQ** — visual draggable curve, real-time FFT spectrum overlay, save/load presets
- **Compressor** — feed-forward design with transparent defaults for clean voice

### 🎛️ Web Control Panel

- **Browser/VR control panel** served on `http://localhost:8765` while PulseForge runs
- Full mixer, mic, and soundboard control from any device on your network
- Real-time VU meters pushed over WebSocket — no page reloads
- Self-contained (Python stdlib + plain ES6), no build step

### 🔊 Stream Routing

- Independent **stream mix** — separate from what you hear
- Route any combination of channels to the stream output
- Creates a virtual microphone that OBS, Discord, and other apps can capture directly
- Your stream audio never touches your speakers — no echo loops

---

## Screenshots

> _The UI is a dark-themed QML mixer with channel strips, an app routing grid, a dedicated mic settings window with a visual EQ, and a soundboard with capture/clip editing._

---

## Getting started

### Prerequisites

| Dependency | Why | Install |
|---|---|---|
| **PipeWire ≥ 1.0** | Audio routing backbone | Pre-installed on most modern distros |
| **WirePlumber** | Session manager for PipeWire | Usually installed alongside PipeWire |
| **Python 3.11+** | Runtime | `python3 --version` to check |
| **PySide6 (Qt 6)** | UI framework | `pip install PySide6` |
| **numpy** | DSP math | `pip install numpy` |
| **ffmpeg** | Soundboard MP3 publish | `sudo pacman -S ffmpeg` / `sudo apt install ffmpeg` |
| **NVIDIA AFX SDK** | AI noise removal (RTX only, optional) | See below |

### Install system libraries

**Arch / CachyOS / Manjaro:**
```bash
sudo pacman -S pipewire wireplumber ffmpeg
```

**Fedora:**
```bash
sudo dnf install pipewire wireplumber ffmpeg
```

**Ubuntu / Debian (24.04+):**
```bash
sudo apt install pipewire wireplumber ffmpeg
```

### Install PulseForge

#### Option A: From source (recommended for now)

```bash
git clone https://github.com/rdv21/pulseforge.git
cd pulseforge
pip install .
```

Then launch with:
```bash
pulseforge
```

#### Option B: Run without installing

```bash
git clone https://github.com/rdv21/pulseforge.git
cd pulseforge
./pulseforge.sh
```

#### Option C: System-wide install with .desktop entry

```bash
git clone https://github.com/rdv21/pulseforge.git
cd pulseforge
sudo make install
```

This installs the app, desktop entry, and icon system-wide. Launch from your application menu or terminal.

---

## How to use

1. **Launch PulseForge** — it auto-detects your input and output devices
2. **Open apps** — they'll appear in the app routing panel. Assign each to a channel (Game, Chat, Media, Aux) from the dropdown
3. **Mix** — adjust volume faders for each channel. The master fader controls overall output
4. **Stream** — click the STREAM button on any channel to send it to the stream mix. OBS and Discord will see "PulseForge Stream Input" as a microphone
5. **Mic settings** — click the gear icon to open the mic processing window. Adjust noise suppression, gate, EQ, and compressor in real-time

### Tips

- **EQ presets**: Save your favorite EQ settings and switch between them instantly
- **Monitor**: Toggle monitor in mic settings to hear yourself through your speakers (useful for testing)
- **Device switching**: Change input/output devices from the main window — PulseForge reconnects automatically

---

## How it works

PulseForge creates virtual PipeWire sinks for each channel (Game, Chat, Media, Aux) and a master mix sink (Gaming). Apps are routed to channel sinks, which are mixed into the master sink, which connects to your hardware output.

For streaming, a separate virtual stream sink collects audio from any channels you enable, and a virtual source makes it available as a microphone input to OBS, Discord, etc.

The mic chain captures audio from your hardware mic via `pw-cat`, processes it through a Python DSP pipeline (AFX noise removal → gate → EQ → compressor), and publishes it as a virtual source that apps can capture.

All processing happens in-process — no external PipeWire filter-chain processes to manage. Parameters update instantly when you move a slider.

A small built-in HTTP server (Python stdlib, no dependencies) serves the web control panel on port 8765 alongside the Qt UI.

---

## Configuration

Settings are stored in `~/.config/pulseforge/`:

- `config.json` — device selections, volumes, mic processing params
- `app-routing.json` — app-to-channel assignments
- `eq-presets/` — saved EQ presets as JSON
- `soundboard/` — soundboard slot bindings (`soundboard.json`)

---

## Requirements detail

- **PipeWire 1.0+** (tested on 1.6.8)
- **WirePlumber** (for session management)
- **Qt 6 with QML** (PySide6 ≥ 6.6)
- **Python 3.11+**
- **numpy ≥ 2.0**
- **ffmpeg** (soundboard MP3 publishing)
- **NVIDIA AFX SDK** (RTX GPUs; required for noise removal)
- KDE Plasma recommended (for system theme integration), but any Qt-compatible desktop works

---

## Contributing

This is a young project. If you want to help:

- Test on different distros and report what works/breaks
- File issues with `pactl list short sinks` and `pw-cli ls Node` output
- UI/UX improvements welcome — the QML is modular and well-organized

---

## License

MIT — see [LICENSE](LICENSE). Build something with it.
