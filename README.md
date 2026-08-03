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

- **Noise cancellation** — speexdsp-powered suppression with continuous intensity control (no robotic artifacts)
- **Noise gate** — RMS-based detection with smooth attack/release, fully closes to zero
- **8-band parametric EQ** — visual draggable curve, real-time FFT spectrum overlay, save/load presets
- **Compressor** — feed-forward design with transparent defaults for clean voice

### 🔊 Stream Routing

- Independent **stream mix** — separate from what you hear
- Route any combination of channels to the stream output
- Creates a virtual microphone that OBS, Discord, and other apps can capture directly
- Your stream audio never touches your speakers — no echo loops

---

## Screenshots

> _Screenshots coming soon — the UI is a dark-themed QML mixer with channel strips, an app routing grid, and a dedicated mic settings window with a visual EQ._

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
| **speexdsp** | Noise suppression | See below |
| **rnnoise** | Legacy noise fallback | Optional |

### Install system libraries

**Arch / CachyOS / Manjaro:**
```bash
sudo pacman -S pipewire wireplumber speexdsp rnnoise
```

**Fedora:**
```bash
sudo dnf install pipewire wireplumber speexdsp rnnoise
```

**Ubuntu / Debian (24.04+):**
```bash
sudo apt install pipewire wireplumber libspeexdsp1 librnnoise0
```

### Install PulseForge

#### Option A: From source (recommended for now)

```bash
git clone https://github.com/aielia/pulseforge.git
cd pulseforge
pip install .
```

Then launch with:
```bash
pulseforge
```

#### Option B: Run without installing

```bash
git clone https://github.com/aielia/pulseforge.git
cd pulseforge
./pulseforge.sh
```

#### Option C: System-wide install with .desktop entry

```bash
git clone https://github.com/aielia/pulseforge.git
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

The mic chain captures audio from your hardware mic via `pw-cat`, processes it through a Python DSP pipeline (noise suppression → gate → EQ → compressor), and publishes it as a virtual source that apps can capture.

All processing happens in-process — no external PipeWire filter-chain processes to manage. Parameters update instantly when you move a slider.

---

## Configuration

Settings are stored in `~/.config/pulseforge/`:

- `config.json` — device selections, volumes, mic processing params
- `app-routing.json` — app-to-channel assignments
- `eq-presets/` — saved EQ presets as JSON

---

## Requirements detail

- **PipeWire 1.0+** (tested on 1.6.8)
- **WirePlumber** (for session management)
- **Qt 6 with QML** (PySide6 ≥ 6.6)
- **Python 3.11+**
- **numpy ≥ 2.0**
- **libspeexdsp** (noise suppression)
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
