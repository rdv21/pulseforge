"""Soundboard backend — 3x3 grid with 3 pages, file binding, and channel recording.

Architecture:
  - SoundboardBackend: manages sound assignments (3 pages × 3x3 grid),
    file playback via pw-play, and per-channel ring buffer recording.
  - ChannelRecorder: captures audio from each PipeWire channel sink
    (game, chat, media, aux, mic) using pw-cat --record, keeping
    a 15-second ring buffer. When the user requests a clip, the
    buffer is trimmed to the selected region and exported as WAV.
  - WaveformEditor: processes captured audio into waveform display data
    (peaks, duration, region markers) for the QML visual editor.

All recording is done by monitoring the existing virtual sinks —
no additional PipeWire nodes are created.
"""
import subprocess
import threading
import time
import wave
import json
import struct
import numpy as np
from pathlib import Path
from typing import Optional
from collections import deque
from dataclasses import dataclass, field

SAMPLE_RATE = 48000
CHANNELS = 2
BUFFER_SECONDS = 15
BUFFER_SAMPLES = SAMPLE_RATE * CHANNELS * BUFFER_SECONDS  # 1,440,000 samples
BUFFER_BYTES = BUFFER_SAMPLES * 4  # float32

# Channel sinks we can record from (excluding stream/gaming which are mixes)
RECORDABLE_CHANNELS = ["game", "chat", "media", "aux", "mic"]


@dataclass
class SoundSlot:
    """A single soundboard slot (one cell in the 3x3 grid)."""
    file_path: str = ""
    name: str = ""
    volume: float = 1.0
    # Playback process handle
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)

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

    Uses pw-cat --record --target=<sink>.monitor to capture audio from a virtual
    sink's monitor source. The ring buffer holds the last BUFFER_SECONDS of audio.
    """

    def __init__(self, channel: str, sink_monitor_name: str):
        self.channel = channel
        self.sink_monitor_name = sink_monitor_name  # e.g. "pulseforge_game.monitor"
        self._buffer = deque(maxlen=BUFFER_SAMPLES)
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        """Start capturing audio from the channel sink."""
        if self._running:
            return
        self._running = True
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

        Uses --container raw to get header-less PCM (no WAV header interference).
        Uses '-' as positional arg for stdout output.
        """
        # Ensure we're targeting a monitor source
        target = self.sink_monitor_name
        if not target.endswith(".monitor"):
            target = f"{target}.monitor"

        cmd = [
            "pw-cat", "--record",
            "--target", target,
            "--format", "f32",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--container", "raw",
            "--latency", "480",
            "-"  # stdout (positional arg, not -o -)
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            chunk_size = 480 * CHANNELS * 4  # 480 frames * 2ch * 4 bytes (float32)
            while self._running and self._proc.poll() is None:
                data = self._proc.stdout.read(chunk_size)
                if not data:
                    break
                samples = np.frombuffer(data, dtype=np.float32)
                with self._lock:
                    self._buffer.extend(samples)
        except Exception as e:
            print(f"  ChannelRecorder[{self.channel}]: capture error: {e}")
        finally:
            self._running = False

    def get_clip(self, duration: float = None) -> Optional[AudioClip]:
        """Get an audio clip from the ring buffer.

        Args:
            duration: Length of clip in seconds. None = full buffer.
        """
        with self._lock:
            if len(self._buffer) == 0:
                return None
            samples = np.array(self._buffer, dtype=np.float32)
            # Reshape to (N, 2) for stereo
            usable = (len(samples) // CHANNELS) * CHANNELS
            samples = samples[:usable].reshape(-1, CHANNELS)
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
        with self._lock:
            if len(self._buffer) == 0:
                return []
            samples = np.array(self._buffer, dtype=np.float32)
            usable = (len(samples) // CHANNELS) * CHANNELS
            samples = samples[:usable].reshape(-1, CHANNELS)
            # Downsample to mono
            mono = samples.mean(axis=1)
            # Split into num_peaks chunks
            chunk = max(1, len(mono) // num_peaks)
            peaks = []
            for i in range(0, len(mono), chunk):
                block = mono[i:i+chunk]
                if len(block) > 0:
                    peak = float(np.max(np.abs(block)))
                    rms = float(np.sqrt(np.mean(block**2)))
                    peaks.append({"peak": peak, "rms": rms})
            return peaks

    @property
    def is_running(self) -> bool:
        return self._running


class SoundboardBackend:
    """Main soundboard controller — manages slots, playback, and recording.

    Manages:
      - 3 pages × 3x3 grid of sound slots (27 total)
      - Sound file playback via pw-play
      - Per-channel ring buffer recording (game, chat, media, aux, mic)
      - Clip export to WAV
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
        # Options: pulseforge_gaming (main mix), pulseforge_game, pulseforge_chat,
        #          pulseforge_media, pulseforge_aux, pulseforge_stream
        self.output_target: str = "pulseforge_gaming"

        # Channel recorders
        self._recorders: dict[str, ChannelRecorder] = {}

        # Load config
        self._load_config()

    def _load_config(self):
        """Load soundboard config from JSON."""
        if not self._config_file.exists():
            return
        try:
            with open(self._config_file) as f:
                data = json.load(f)
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
        data = {"pages": []}
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

    def play_sound(self, page: int, index: int):
        """Play the sound assigned to a slot, routed to the output target."""
        slot = self.get_slot(page, index)
        if not slot or not slot.is_assigned:
            return
        self.stop_playback(page, index)
        try:
            cmd = ["pw-play", slot.file_path]
            if self.output_target:
                cmd.extend(["--target", self.output_target])
            slot._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"  Soundboard: playback error: {e}")

    def stop_playback(self, page: int, index: int):
        """Stop playback of a slot."""
        slot = self.get_slot(page, index)
        if slot and slot._proc:
            try:
                slot._proc.terminate()
                slot._proc.wait(timeout=1)
            except Exception:
                try:
                    slot._proc.kill()
                except Exception:
                    pass
            slot._proc = None

    def is_playing(self, page: int, index: int) -> bool:
        slot = self.get_slot(page, index)
        if slot and slot._proc:
            return slot._proc.poll() is None
        return False

    # ─── Channel Recording ───

    def start_recording(self, channel: str, sink_monitor_name: str):
        """Start recording a channel's audio into a ring buffer.

        Args:
            channel: Channel name (game, chat, media, aux, mic)
            sink_monitor_name: Monitor source name (e.g. pulseforge_game.monitor)
        """
        if channel in self._recorders and self._recorders[channel].is_running:
            return
        recorder = ChannelRecorder(channel, sink_monitor_name)
        recorder.start()
        self._recorders[channel] = recorder

    def stop_recording(self, channel: str):
        """Stop recording a channel."""
        if channel in self._recorders:
            self._recorders[channel].stop()
            del self._recorders[channel]

    def stop_all_recording(self):
        """Stop all channel recordings."""
        for channel in list(self._recorders.keys()):
            self.stop_recording(channel)

    def get_clip(self, channel: str, duration: float = None) -> Optional[AudioClip]:
        """Get a captured clip from a channel's ring buffer."""
        recorder = self._recorders.get(channel)
        if not recorder:
            return None
        return recorder.get_clip(duration)

    def get_waveform(self, channel: str, num_peaks: int = 200) -> list:
        """Get waveform peaks for a channel's current buffer."""
        recorder = self._recorders.get(channel)
        if not recorder:
            return []
        return recorder.get_waveform(num_peaks)

    def export_clip_wav(self, clip: AudioClip, output_path: str) -> bool:
        """Export an AudioClip to a WAV file (trimmed to clip.trim_start..trim_end)."""
        try:
            samples = clip.trimmed_samples
            # Convert float32 to int16
            int_samples = (samples * 32767).clip(-32768, 32767).astype(np.int16)
            with wave.open(output_path, "w") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(clip.sample_rate)
                wf.writeframes(int_samples.tobytes())
            return True
        except Exception as e:
            print(f"  Soundboard: WAV export error: {e}")
            return False

    def get_recording_channels(self) -> list:
        """Return list of currently recording channel names."""
        return [ch for ch, r in self._recorders.items() if r.is_running]

    # ─── Clip Management ───

    def capture_clip(self, channel: str, duration: float = None) -> Optional[AudioClip]:
        """Capture a clip and store it as the current clip for editing."""
        self._current_clip = self.get_clip(channel, duration)
        if self._current_clip:
            print(f"  Soundboard: captured {self._current_clip.duration:.1f}s clip from {channel}")
        return self._current_clip

    def get_current_clip(self) -> Optional[AudioClip]:
        """Return the currently captured clip for editing."""
        return getattr(self, '_current_clip', None)

    def get_clip_waveform(self, num_peaks: int = 200) -> list:
        """Return waveform peaks for the current clip (not the live buffer)."""
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

    def play_clip_preview(self) -> bool:
        """Play the current trimmed clip via a temp WAV + pw-play (routed to output target)."""
        clip = self.get_current_clip()
        if not clip:
            return False
        self.stop_clip_preview()
        tmp_path = str(self._config_dir / "_preview.wav")
        if not self.export_clip_wav(clip, tmp_path):
            return False
        try:
            cmd = ["pw-play", tmp_path]
            if self.output_target:
                cmd.extend(["--target", self.output_target])
            self._clip_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            print(f"  Soundboard: clip preview error: {e}")
            return False

    def stop_clip_preview(self):
        """Stop clip preview playback."""
        proc = getattr(self, '_clip_proc', None)
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            self._clip_proc = None

    def is_clip_playing(self) -> bool:
        proc = getattr(self, '_clip_proc', None)
        return proc is not None and proc.poll() is None

    def clear_clip(self):
        """Discard the current clip."""
        self.stop_clip_preview()
        self._current_clip = None

    def set_output_target(self, target: str):
        """Set the output target for soundboard playback.

        Args:
            target: PipeWire sink name (e.g. pulseforge_gaming, pulseforge_stream)
        """
        self.output_target = target

    def get_output_target(self) -> str:
        return self.output_target

    def cleanup(self):
        """Stop all playback and recording."""
        for page in range(3):
            for i in range(9):
                self.stop_playback(page, i)
        self.stop_all_recording()
