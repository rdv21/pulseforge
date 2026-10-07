"""Soundboard backend — 3x3 grid with 3 pages, file binding, and channel recording.

Architecture:
  - SoundboardBackend: manages sound assignments (3 pages × 3x3 grid),
    file playback via pw-play, and per-channel ring buffer recording.
  - ChannelRecorder: captures audio from each PipeWire channel sink
    (game, chat, media, aux, mic) using pw-cat --record, keeping
    a 15-second ring buffer. All channels record constantly.
  - AudioClip: captured audio sample with trim region for editing.
  - Publish flow: capture → edit/trim → publish to MP3 → optionally
    assign to a soundboard slot.

All recording is done by monitoring the existing virtual sinks —
no additional PipeWire nodes are created.
"""
import subprocess
import threading
import time
import wave
import json
import os
import numpy as np
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

SAMPLE_RATE = 48000
CHANNELS = 2
BUFFER_SECONDS = 15
BUFFER_SAMPLES = SAMPLE_RATE * CHANNELS * BUFFER_SECONDS  # 1,440,000 samples

# Channel sinks we can record from (excluding stream/gaming which are mixes)
RECORDABLE_CHANNELS = ["game", "chat", "media", "aux", "mic"]

# Where published clips are saved
PUBLISH_DIR = Path.home() / "Music" / "Soundboard REC"


@dataclass
class SoundSlot:
    """A single soundboard slot (one cell in the 3x3 grid)."""
    file_path: str = ""
    name: str = ""
    volume: float = 1.0
    # Playback process handles (multiple for dual-output to main+stream)
    _procs: list = field(default_factory=list, repr=False)

    @property
    def is_assigned(self) -> bool:
        return bool(self.file_path)


@dataclass
class AudioClip:
    """A captured audio clip from a channel recording."""
    channel: str
    samples: np.ndarray  # float32, shape (N, 2) for stereo
    sample_rate: int = SAMPLE_RATE
    start_time: float = 0.0  # timestamp when clip was captured
    duration: float = 0.0
    # Edit region (in seconds)
    trim_start: float = 0.0
    trim_end: float = 0.0

    @property
    def trimmed_samples(self) -> np.ndarray:
        """Return samples within the trim region."""
        s = int(self.trim_start * self.sample_rate)
        e = int(self.trim_end * self.sample_rate)
        s = max(0, min(s, len(self.samples)))
        e = max(s, min(e, len(self.samples)))
        return self.samples[s:e]

    @property
    def trimmed_duration(self) -> float:
        return self.trim_end - self.trim_start


class ChannelRecorder:
    """Records a 15-second ring buffer from a PipeWire channel sink monitor.

    Uses pw-cat --record --target=<source> to capture audio. For sink channels
    (game/chat/media/aux) this is the sink's .monitor source. For mic, it's
    the pulseforge.mic.processed source directly (1 channel mono).
    The ring buffer holds the last BUFFER_SECONDS of audio.
    Always running once started — captures continuously.
    """

    def __init__(self, channel: str, source_name: str, channels: int = CHANNELS):
        self.channel = channel
        self.source_name = source_name  # e.g. "pulseforge_game.monitor" or "pulseforge.mic.processed"
        self.channels = channels  # 2 for stereo monitors, 1 for mono mic
        # Raw interleaved float32 ring buffer. Storing raw bytes instead of a
        # deque of boxed Python floats keeps each recorder at a fixed
        # (SAMPLE_RATE * channels * BUFFER_SECONDS * 4) bytes slice of memory
        # and makes snapshots a cheap memcpy instead of an O(N) object walk.
        self._max_samples = SAMPLE_RATE * channels * BUFFER_SECONDS
        self._buf = bytearray(self._max_samples * 4)
        self._write_pos = 0
        self._filled = 0
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Start capturing audio from the channel sink."""
        if self._running:
            return
        self._running = True
        with self._lock:
            self._buf = bytearray(self._max_samples * 4)
            self._write_pos = 0
            self._filled = 0
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop capturing audio."""
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _capture_loop(self):
        """Capture loop: read raw float32 from pw-cat stdout into ring buffer.

        Uses the VU meter technique: start pw-cat without --target, then
        redirect the source-output to the monitor source using pactl
        move-source-output. This is required because --target doesn't
        work for PulseAudio compat monitor sources.
        """
        target = self.source_name
        # Only append .monitor if target is a sink name (not already a source)
        if not target.endswith(".monitor") and not target.startswith("pulseforge.mic"):
            target = f"{target}.monitor"

        app_tag = f"pulseforge_sb_{self.channel}"
        try:
            # Start pw-cat WITHOUT --target (connects to default source)
            self._proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--format", "f32",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", str(self.channels),
                    "--container", "raw",
                    "--latency", "480",
                    "-P", f"application.name={app_tag}",
                    "-P", f"node.name={app_tag}",
                    "-",  # stdout
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )

            # Wait for pw-cat to create its source-output, then redirect
            time.sleep(0.5)
            if not self._redirect_to_monitor(target, app_tag):
                # Retry a few times
                for _ in range(5):
                    time.sleep(0.3)
                    if self._redirect_to_monitor(target, app_tag):
                        break
                else:
                    print(f"  ChannelRecorder[{self.channel}]: failed to redirect to {target}")

            chunk_size = 480 * self.channels * 4  # 480 frames * Nch * 4 bytes (float32)
            while self._running and self._proc.poll() is None:
                data = self._proc.stdout.read(chunk_size)
                if not data:
                    break
                with self._lock:
                    self._write_bytes(data)
        except Exception as e:
            print(f"  ChannelRecorder[{self.channel}]: capture error: {e}")
        finally:
            self._running = False

    def _write_bytes(self, data: bytes):
        """Append raw f32 bytes into the ring buffer (called under self._lock)."""
        n = len(data) // 4  # sample frames (drop any partial trailing sample)
        if n == 0:
            return
        data = data[:n * 4]
        mv = memoryview(self._buf)
        pos = self._write_pos  # sample index
        cap = self._max_samples
        if pos + n <= cap:
            mv[pos * 4:(pos + n) * 4] = data
        else:
            first = cap - pos
            mv[pos * 4:] = data[:first * 4]
            mv[0:(n - first) * 4] = data[first * 4:]
        self._write_pos = (pos + n) % cap
        self._filled = min(cap, self._filled + n)

    def _snapshot(self) -> Optional[np.ndarray]:
        """Return the ring buffer contents oldest-first as a float32 array.

        Cheap: copies raw bytes out under the lock, then views them as f32
        (zero-copy) so callers get a stable snapshot without holding the lock
        across numpy work. Returns None when the recorder has no data yet.
        """
        with self._lock:
            filled = self._filled
            if filled == 0:
                return None
            mv = memoryview(self._buf)
            start = self._write_pos - filled  # sample index; may be negative
            if start >= 0:
                raw = bytes(mv[start * 4:(start + filled) * 4])
            else:
                # Wrapped ring: oldest bytes live at the tail of the buffer.
                raw = bytes(mv[start * 4:]) + bytes(mv[0:self._write_pos * 4])
        return np.frombuffer(raw, dtype=np.float32)

    def _redirect_to_monitor(self, target_source: str, app_tag: str) -> bool:
        """Find our pw-cat source-output by app tag and redirect to target source.

        Must match the EXACT app tag to avoid grabbing another channel's recorder.
        """
        try:
            r = subprocess.run(
                ["pactl", "list", "source-outputs"],
                capture_output=True, text=True, timeout=5
            )
            blocks = r.stdout.split("Source Output #")
            for block in blocks[1:]:
                idx = block.split("\n")[0].strip()
                # Match EXACT tag — use quotes to avoid partial matches
                if f'application.name = "{app_tag}"' in block:
                    r2 = subprocess.run(
                        ["pactl", "move-source-output", idx, target_source],
                        capture_output=True, text=True, timeout=5
                    )
                    if r2.returncode == 0:
                        print(f"  ChannelRecorder[{self.channel}]: redirected to {target_source}")
                        return True
                    else:
                        print(f"  ChannelRecorder[{self.channel}]: move failed: {r2.stderr.strip()}")
                        return False
            return False
        except Exception as e:
            print(f"  ChannelRecorder[{self.channel}]: redirect error: {e}")
            return False

    def get_clip(self, duration: float = None) -> Optional[AudioClip]:
        """Get an audio clip from the ring buffer.

        Args:
            duration: Length of clip in seconds. None = full buffer (15s).
        """
        samples = self._snapshot()
        if samples is None:
            return None
        ch = self.channels
        usable = (len(samples) // ch) * ch
        samples = samples[:usable].reshape(-1, ch)
        if duration is not None:
            n = int(duration * SAMPLE_RATE)
            samples = samples[-n:]
        clip = AudioClip(
            channel=self.channel,
            samples=samples,
            start_time=time.time() - len(samples) / SAMPLE_RATE,
            duration=len(samples) / SAMPLE_RATE,
        )
        clip.trim_end = clip.duration
        return clip

    def get_waveform(self, num_peaks: int = 200) -> list:
        """Return waveform peaks for QML display.

        Returns list of {peak, rms} values, 0.0-1.0 normalized.
        """
        samples = self._snapshot()
        if samples is None or len(samples) == 0:
            return []
        ch = self.channels
        usable = (len(samples) // ch) * ch
        samples = samples[:usable].reshape(-1, ch)
        mono = samples.mean(axis=1) if ch > 1 else samples
        # Vectorized peak/RMS per bucket — no Python per-block loop.
        chunk = max(1, len(mono) // num_peaks)
        nblocks = len(mono) // chunk
        if nblocks < 1:
            return []
        trimmed = mono[:nblocks * chunk].reshape(nblocks, chunk)
        peaks = np.abs(trimmed).max(axis=1)
        rms = np.sqrt((trimmed.astype(np.float32) ** 2).mean(axis=1))
        return [{"peak": float(p), "rms": float(r)}
                for p, r in zip(peaks, rms)]

    @property
    def is_running(self) -> bool:
        return self._running


class SoundboardBackend:
    """Main soundboard controller — manages slots, playback, and recording.

    Manages:
      - 3 pages × 3x3 grid of sound slots (27 total)
      - Sound file playback via pw-play (dual-output to main mix + stream)
      - Per-channel ring buffer recording (game, chat, media, aux, mic)
        — ALL channels record constantly, always-on
      - Clip capture → trim/edit → publish to MP3 → slot assignment
    """

    def __init__(self, config_dir: Path):
        self._config_dir = config_dir / "soundboard"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._config_file = self._config_dir / "soundboard.json"

        # 3 pages × 9 slots
        self.pages: list[list[SoundSlot]] = [
            [SoundSlot() for _ in range(9)] for _ in range(3)
        ]
        self.current_page = 0

        # Output target for soundboard playback (default: main mix)
        self.output_target: str = "pulseforge_gaming"

        # Channel recorders — always running
        self._recorders: dict[str, ChannelRecorder] = {}

        # Current clip being edited
        self._current_clip: Optional[AudioClip] = None

        # Last published clip (for slot assignment flow)
        self._last_published_path: str = ""
        self._last_published_name: str = ""

        # Last name conflict (so the UI can explain why a save was blocked)
        self._last_publish_conflict: dict = {}

        # Recording enabled (persisted, starts with this state)
        self.recording_enabled: bool = True

        # Load config
        self._load_config()

    def _load_config(self):
        """Load soundboard config from JSON."""
        if not self._config_file.exists():
            return
        try:
            with open(self._config_file) as f:
                data = json.load(f)
            self.recording_enabled = data.get("recording_enabled", True)
            for page_idx, page_data in enumerate(data.get("pages", [])):
                if page_idx >= 3:
                    break
                for slot_idx, slot_data in enumerate(page_data.get("slots", [])):
                    if slot_idx >= 9:
                        break
                    self.pages[page_idx][slot_idx].file_path = slot_data.get("file_path", "")
                    self.pages[page_idx][slot_idx].name = slot_data.get("name", "")
                    self.pages[page_idx][slot_idx].volume = slot_data.get("volume", 1.0)
        except Exception as e:
            print(f"  Soundboard: config load error: {e}")

    def _save_config(self):
        """Save soundboard config to JSON."""
        data = {"recording_enabled": self.recording_enabled, "pages": []}
        for page in self.pages:
            page_data = {"slots": []}
            for slot in page:
                page_data["slots"].append({
                    "file_path": slot.file_path,
                    "name": slot.name,
                    "volume": slot.volume,
                })
            data["pages"].append(page_data)
        try:
            with open(self._config_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"  Soundboard: config save error: {e}")

    def get_slot(self, page: int, index: int) -> Optional[SoundSlot]:
        if 0 <= page < 3 and 0 <= index < 9:
            return self.pages[page][index]
        return None

    def assign_sound(self, page: int, index: int, file_path: str, name: str = ""):
        """Bind a sound file to a slot."""
        slot = self.get_slot(page, index)
        if slot:
            slot.file_path = file_path
            slot.name = name or Path(file_path).stem
            self._save_config()

    def clear_slot(self, page: int, index: int):
        """Remove a sound from a slot."""
        slot = self.get_slot(page, index)
        if slot:
            self.stop_playback(page, index)
            slot.file_path = ""
            slot.name = ""
            self._save_config()

    # ─── Playback ───

    _DUAL_OUTPUT_TARGETS = {"pulseforge_gaming"}

    def _get_playback_targets(self) -> list:
        """Determine which PipeWire sinks to play to based on output_target."""
        if self.output_target in self._DUAL_OUTPUT_TARGETS:
            return ["pulseforge_gaming", "pulseforge_stream"]
        return [self.output_target]

    def _reap(self):
        """Reap exited pw-play children so they don't linger as zombies.

        Popen.poll() calls waitpid(WNOHANG), which reaps the child. Without
        this, a sound that finishes on its own leaves a defunct process behind.
        """
        for page in self.pages:
            for slot in page:
                if slot._procs:
                    slot._procs = [p for p in slot._procs if p.poll() is None]
        clip_procs = getattr(self, "_clip_procs", None)
        if clip_procs:
            for p in clip_procs:
                try:
                    p.poll()  # reap if it already exited
                except Exception:
                    pass
            self._clip_procs = [p for p in clip_procs if p.poll() is None]

    def play_sound(self, page: int, index: int):
        """Play the sound assigned to a slot, routed to the output target(s)."""
        slot = self.get_slot(page, index)
        if not slot or not slot.is_assigned:
            return
        self._reap()
        self.stop_playback(page, index)
        for target in self._get_playback_targets():
            try:
                cmd = ["pw-play", slot.file_path, "--target", target]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                slot._procs.append(proc)
            except Exception as e:
                print(f"  Soundboard: playback error ({target}): {e}")

    def stop_playback(self, page: int, index: int):
        """Stop playback of a slot."""
        slot = self.get_slot(page, index)
        if slot and slot._procs:
            for proc in slot._procs:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            slot._procs.clear()

    def is_playing(self, page: int, index: int) -> bool:
        self._reap()
        slot = self.get_slot(page, index)
        if slot and slot._procs:
            return any(p.poll() is None for p in slot._procs)
        return False

    # ─── Always-On Channel Recording ───

    def start_all_recording(self, channel_monitor_map: dict):
        """Start recording all channels at once (if recording is enabled).

        Staggers start by 1s per channel to avoid move-source-output races.
        """
        if not self.recording_enabled:
            print(f"  Soundboard: recording disabled, skipping")
            return
        import threading
        def _start_channel(ch, source_name, channels, delay):
            time.sleep(delay)
            if ch not in self._recorders or not self._recorders[ch].is_running:
                recorder = ChannelRecorder(ch, source_name, channels)
                recorder.start()
                self._recorders[ch] = recorder
                print(f"  Soundboard: always-on recording started for '{ch}' ({channels}ch)")

        delay = 0.0
        for channel, (source_name, channels) in channel_monitor_map.items():
            threading.Thread(
                target=_start_channel, args=(channel, source_name, channels, delay),
                daemon=True
            ).start()
            delay += 1.0  # 1s stagger between channels

    def stop_all_recording(self):
        """Stop all channel recordings."""
        for channel in list(self._recorders.keys()):
            self._recorders[channel].stop()
            print(f"  Soundboard: recording stopped for '{channel}'")
        self._recorders.clear()

    def get_recording_channels(self) -> list:
        """Return list of currently recording channel names."""
        return [ch for ch, r in self._recorders.items() if r.is_running]

    def is_channel_recording(self, channel: str) -> bool:
        rec = self._recorders.get(channel)
        return rec is not None and rec.is_running

    def get_waveform(self, channel: str, num_peaks: int = 200) -> list:
        """Get waveform peaks for a channel's current buffer."""
        recorder = self._recorders.get(channel)
        if not recorder:
            return []
        return recorder.get_waveform(num_peaks)

    # ─── Clip Capture & Editing ───

    def capture_clip(self, channel: str, duration: float = None) -> Optional[AudioClip]:
        """Capture a clip from the ring buffer for editing.

        Takes a snapshot of the last 15 seconds (or specified duration).
        """
        recorder = self._recorders.get(channel)
        if not recorder:
            print(f"  Soundboard: no recorder for channel '{channel}'")
            return None
        self._current_clip = recorder.get_clip(duration)
        if self._current_clip:
            print(f"  Soundboard: captured {self._current_clip.duration:.1f}s clip from {channel}")
        return self._current_clip

    def get_current_clip(self) -> Optional[AudioClip]:
        """Return the currently captured clip for editing."""
        return self._current_clip

    def get_clip_waveform(self, num_peaks: int = 200) -> list:
        """Return waveform peaks for the current clip."""
        clip = self.get_current_clip()
        if not clip:
            return []
        mono = clip.samples.mean(axis=1) if clip.samples.ndim == 2 else clip.samples
        chunk = max(1, len(mono) // num_peaks)
        peaks = []
        for i in range(0, len(mono), chunk):
            block = mono[i:i+chunk]
            if len(block) > 0:
                peak = float(np.max(np.abs(block)))
                rms = float(np.sqrt(np.mean(block**2)))
                peaks.append({"peak": peak, "rms": rms})
        return peaks

    def set_clip_trim(self, trim_start: float, trim_end: float):
        """Set the trim region on the current clip (seconds)."""
        clip = self.get_current_clip()
        if not clip:
            return
        clip.trim_start = max(0.0, min(trim_start, clip.duration))
        clip.trim_end = max(clip.trim_start, min(trim_end, clip.duration))

    def get_clip_info(self) -> dict:
        """Return info about the current clip for QML."""
        clip = self.get_current_clip()
        if not clip:
            return {"available": False, "duration": 0, "channel": "", "trim_start": 0, "trim_end": 0}
        return {
            "available": True,
            "duration": clip.duration,
            "channel": clip.channel,
            "trim_start": clip.trim_start,
            "trim_end": clip.trim_end,
            "trimmed_duration": clip.trimmed_duration,
        }

    def _export_clip_wav(self, clip: AudioClip, output_path: str) -> bool:
        """Export an AudioClip to a WAV file (trimmed to clip.trim_start..trim_end)."""
        try:
            samples = clip.trimmed_samples
            # Convert float32 to int16
            int_samples = (samples * 32767).clip(-32768, 32767).astype(np.int16)
            with wave.open(output_path, "w") as wf:
                wf.setnchannels(samples.shape[1] if samples.ndim == 2 else 1)
                wf.setsampwidth(2)
                wf.setframerate(clip.sample_rate)
                wf.writeframes(int_samples.tobytes())
            return True
        except Exception as e:
            print(f"  Soundboard: WAV export error: {e}")
            return False

    def play_clip_preview(self) -> bool:
        """Play the current trimmed clip via a temp WAV + pw-play."""
        clip = self.get_current_clip()
        if not clip:
            return False
        self.stop_clip_preview()
        tmp_path = str(self._config_dir / "_preview.wav")
        if not self._export_clip_wav(clip, tmp_path):
            return False
        self._clip_procs = []
        for target in self._get_playback_targets():
            try:
                cmd = ["pw-play", tmp_path, "--target", target]
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._clip_procs.append(proc)
            except Exception as e:
                print(f"  Soundboard: clip preview error ({target}): {e}")
        return bool(self._clip_procs)

    def stop_clip_preview(self):
        """Stop clip preview playback."""
        for proc in getattr(self, '_clip_procs', []):
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._clip_procs = []

    def is_clip_playing(self) -> bool:
        procs = getattr(self, '_clip_procs', [])
        if procs:
            for p in procs:
                try:
                    p.poll()  # reap finished previews so they don't linger as zombies
                except Exception:
                    pass
            self._clip_procs = [p for p in procs if p.poll() is None]
        return any(p.poll() is None for p in getattr(self, '_clip_procs', []))

    def clear_clip(self):
        """Discard the current clip."""
        self.stop_clip_preview()
        self._current_clip = None

    # ─── Publish to MP3 ───

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Make a user-supplied name filesystem-safe (no extension handling)."""
        safe = "".join(c for c in name if c not in "/\\:*?\"<>|").strip()
        return safe or "clip"

    @staticmethod
    def _auto_base_name(clip: "AudioClip") -> str:
        """Default name when the user leaves the name field empty."""
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{clip.channel or 'clip'}_{timestamp}"

    def _unique_name(self, base: str, ext: str = "mp3") -> str:
        """Return a non-colliding filename stem: 'name', 'name (1)', 'name (2)'..."""
        if not (PUBLISH_DIR / f"{base}.{ext}").exists():
            return base
        i = 1
        while (PUBLISH_DIR / f"{base} ({i}).{ext}").exists():
            i += 1
        return f"{base} ({i})"

    def resolve_publish_name(self, custom_name: str = "") -> dict:
        """Resolve the final MP3 filename without writing anything.

        Lets the UI check for collisions *before* saving so it can offer an
        in-app rename / overwrite dialog instead of silently replacing a file.
        """
        clip = self.get_current_clip()
        if clip is None:
            return {
                "available": False, "name": "", "stem": "", "exists": False,
                "suggestion": "", "suggestion_stem": "", "dir": str(PUBLISH_DIR),
            }
        if custom_name and custom_name.strip():
            base = self._sanitize_name(custom_name)
        else:
            base = self._auto_base_name(clip)
        name = f"{base}.mp3"
        exists = (PUBLISH_DIR / name).exists()
        suggestion_stem = self._unique_name(base, "mp3") if exists else base
        return {
            "available": True,
            "name": name,
            "stem": base,
            "exists": exists,
            "suggestion": f"{suggestion_stem}.mp3",
            "suggestion_stem": suggestion_stem,
            "dir": str(PUBLISH_DIR),
        }

    def _encode_clip_mp3(self, clip: "AudioClip", mp3_path: Path) -> bool:
        """Render the trimmed clip to an MP3 file at mp3_path. Returns success."""
        tmp_wav = str(self._config_dir / "_publish.wav")
        if not self._export_clip_wav(clip, tmp_wav):
            return False
        try:
            cmd = [
                "ffmpeg", "-y", "-i", tmp_wav,
                "-codec:a", "libmp3lame", "-b:a", "192k",
                str(mp3_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                print(f"  Soundboard: ffmpeg error: {result.stderr[:200]}")
                return False
        except Exception as e:
            print(f"  Soundboard: MP3 publish error: {e}")
            return False
        finally:
            try:
                os.unlink(tmp_wav)
            except Exception:
                pass
        return True

    def publish_clip(self, custom_name: str = "", overwrite: bool = False) -> Optional[str]:
        """Export the current trimmed clip as MP3 to ~/Music/Soundboard REC/.

        Args:
            custom_name: User-specified filename (without extension). If empty,
                        uses channel_YYYY-MM-DD_HH-MM-SS.mp3.
            overwrite:   When False (default) a name collision aborts the save
                        instead of replacing the existing file. Callers should
                        first use resolve_publish_name() to detect collisions.
        Returns the path to the MP3 file, or None on failure/conflict.
        The published clip is stored as _last_published for slot assignment.
        """
        clip = self.get_current_clip()
        if not clip:
            return None

        # Ensure publish directory exists
        PUBLISH_DIR.mkdir(parents=True, exist_ok=True)

        info = self.resolve_publish_name(custom_name)
        if not info["available"]:
            return None
        mp3_name = info["name"]
        mp3_path = PUBLISH_DIR / mp3_name

        # Refuse to clobber unless the caller explicitly asked to overwrite.
        if mp3_path.exists() and not overwrite:
            self._last_publish_conflict = dict(info)
            print(f"  Soundboard: publish blocked — '{mp3_name}' already exists "
                  f"(suggest '{info['suggestion']}')")
            return None

        if not self._encode_clip_mp3(clip, mp3_path):
            return None

        self._last_publish_conflict = {}
        self._last_published_path = str(mp3_path)
        self._last_published_name = Path(mp3_path).stem
        print(f"  Soundboard: published {mp3_path}")
        return str(mp3_path)

    def get_last_conflict(self) -> dict:
        """Return the most recent blocked-save info ({"name":..., "suggestion":...})."""
        return dict(self._last_publish_conflict)

    def get_last_published(self) -> dict:
        """Return info about the last published clip for slot assignment."""
        return {
            "path": self._last_published_path,
            "name": self._last_published_name,
            "available": bool(self._last_published_path),
        }

    def assign_published_clip(self, page: int, index: int) -> bool:
        """Assign the last published MP3 to a soundboard slot.

        Returns True on success.
        """
        if not self._last_published_path:
            return False
        if not Path(self._last_published_path).exists():
            return False
        self.assign_sound(page, index, self._last_published_path, self._last_published_name)
        print(f"  Soundboard: assigned '{self._last_published_name}' to page {page} slot {index}")
        return True

    def clear_published(self):
        """Clear the last published clip (cancel assignment mode)."""
        self._last_published_path = ""
        self._last_published_name = ""

    # ─── Output Target ───

    def set_output_target(self, target: str):
        self.output_target = target

    def is_recording_enabled(self) -> bool:
        return self.recording_enabled

    def set_recording_enabled(self, enabled: bool):
        """Toggle recording on/off. Starts/stops all channel recorders."""
        self.recording_enabled = enabled
        self._save_config()
        if enabled:
            print(f"  Soundboard: recording enabled")
        else:
            print(f"  Soundboard: recording disabled")
            self.stop_all_recording()

    def get_output_target(self) -> str:
        return self.output_target

    # ─── Lifecycle ───

    def cleanup(self):
        """Stop all playback and recording."""
        for page in range(3):
            for i in range(9):
                self.stop_playback(page, i)
        self.stop_clip_preview()
        self.stop_all_recording()
        # Remove transient scratch WAVs so they don't litter the config dir
        for scratch in ("_preview.wav", "_publish.wav"):
            try:
                (self._config_dir / scratch).unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
