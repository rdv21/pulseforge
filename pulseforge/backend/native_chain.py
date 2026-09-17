"""Native Python mic processing chain.

Captures audio from the hardware mic via pw-cat --record, processes it
through NVIDIA AFX noise removal → gate → EQ → compressor in Python,
and plays it back via pw-cat --playback to create the
pulseforge.mic.processed node.

All parameters update in real-time — no process restarts needed.

Audio format: float32, 48kHz, mono.
Buffer size: 480 samples (10ms) — matches AFX frame size.
"""
import subprocess
import threading
import time
import numpy as np
from typing import Optional

from . import dsp
from . import pipewire_ctl as pw
from .process_manager import get_manager

# Audio settings
SAMPLE_RATE = 48000
CHANNELS = 1  # Hardware mic is mono — capture mono, process mono, output mono
BUFFER_SAMPLES = 480  # 10ms — matches AFX frame size
BUFFER_BYTES = BUFFER_SAMPLES * CHANNELS * 4  # float32 = 4 bytes

# Spectrum analyzer settings
FFT_SIZE = 2048      # ~43ms window at 48kHz → ~23Hz resolution
HOP_SIZE = 3072      # Update spectrum every ~64ms (~15fps) — sufficient for visual display
NUM_SPEC_POINTS = 64  # Number of points sent to QML


def _hann_window(n: int) -> np.ndarray:
    """Hann window for FFT."""
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))


# Cached FFT assets — computed once at import, reused for every spectrum call
_HANN = _hann_window(FFT_SIZE)
_RFFFREQ = np.fft.rfftfreq(FFT_SIZE, 1.0 / SAMPLE_RATE)

# Pre-computed log-spaced target frequencies and their nearest bin indices
_LOG_MIN = np.log(20)
_LOG_MAX = np.log(20000)
_LOG_T = np.arange(NUM_SPEC_POINTS, dtype=np.float64) / (NUM_SPEC_POINTS - 1)
_LOG_FREQS = np.exp(_LOG_MIN + _LOG_T * (_LOG_MAX - _LOG_MIN))
_SPEC_BIN_IDX = np.argmin(np.abs(_RFFFREQ[:, None] - _LOG_FREQS[None, :]), axis=0)


def _compute_spectrum(samples: np.ndarray, fft_size: int, num_points: int) -> list:
    """Compute log-spaced magnitude spectrum in dB.

    Returns:
        List of {freq, level} dicts. level normalized to 0.0..1.0.
        Scale: -60dB..+6dB mapped to 0.0..1.0.
        Below -60dB shows as 0.
    """
    if len(samples) < fft_size:
        samples = np.pad(samples, (0, fft_size - len(samples)))

    # Apply Hann window (cached at module level)
    windowed = samples[:fft_size] * _HANN

    # FFT — normalized by N/2 so full-scale sine = 0dB
    spectrum = np.fft.rfft(windowed)
    magnitude = np.abs(spectrum[:fft_size // 2 + 1]) / (fft_size / 2.0)

    # Convert to dB
    magnitude = np.maximum(magnitude, 1e-10)
    db = 20.0 * np.log10(magnitude)

    # Resample to log-spaced points using pre-computed indices
    points = []
    for i in range(num_points):
        idx = _SPEC_BIN_IDX[i]
        lo = max(0, idx - 1)
        hi = min(len(db) - 1, idx + 1)
        level = float(np.mean(db[lo:hi + 1]))
        # Fixed scale: -60dB..+6dB → 0.0..1.0
        level = max(-60.0, min(6.0, level))
        level = (level + 60.0) / 66.0
        points.append({"freq": float(_LOG_FREQS[i]), "level": level})

    return points


# Node names
CAPTURE_TAG = "pulseforge.mic.native.capture"
PLAYBACK_TAG = "pulseforge.mic.processed"

PROCESS_CATEGORY = "native-mic"


class NativeMicChain:
    """Python-native mic processing chain with NVIDIA AFX noise removal."""

    def __init__(self):
        self.gate = dsp.GateProcessor()
        self.eq = dsp.EQProcessor()
        self.emi_filter = dsp.EMIFilter(fundamental_hz=240.0, num_harmonics=2, bin_radius=1)

        # Tonal noise notch filters — removes line noise / coil whine / USB EMI
        # before AFX sees the signal, so AFX doesn't have to fight tonal artifacts
        self.notch_chain = dsp.NotchFilterChain(
            freqs=[128.0, 218.0],  # tuned to measured noise peaks
            q=15.0,
            block_size=BUFFER_SAMPLES,
        )

        # NVIDIA AFX — the only noise processor
        self._afx = dsp.AFXNoiseProcessor(
            effect_mode='denoiser_v2',
            intensity=0.7,
            enabled=True,
            frame_samples=BUFFER_SAMPLES,
        )
        self.noise = self._afx
        self.compressor = dsp.CompressorProcessor()

        self._capture_proc = None
        self._playback_proc = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Pre-gain no longer needed — AFX handles raw mic levels well
        self._pre_gain = 1.0
        self._post_atten = 1.0

        # Configured input device (set by set_input_device / loaded from config)
        self._configured_input_device = None

        # Peak level for VU (read by external callers)
        self._peak = 0.0
        self._peak_lock = threading.Lock()

        # Spectrum analyzer state
        self._spec_buffer = np.zeros(FFT_SIZE, dtype=np.float32)
        self._spec_write_pos = 0
        self._spec_hop_counter = 0
        self._spec_rolled = np.zeros(FFT_SIZE, dtype=np.float32)  # pre-allocated for np.roll replacement
        self._spectrum: list = []
        self._spec_lock = threading.Lock()

    @property
    def peak(self) -> float:
        with self._peak_lock:
            return self._peak

    def start(self):
        """Start the capture → process → playback chain."""
        if self._running:
            return

        # PipeWire latency environment — force small buffers for low latency
        pw_env = {**__import__('os').environ, "PIPEWIRE_LATENCY": f"{BUFFER_SAMPLES}/{SAMPLE_RATE}"}

        # Start capture: pw-cat --record from hardware mic
        capture_cmd = [
            "pw-cat", "--record",
            "--format", "f32",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--latency", str(BUFFER_SAMPLES),  # 480 samples = 10ms
            "-P", f"application.name={CAPTURE_TAG}",
            "-P", f"node.name={CAPTURE_TAG}",
            "-P", "session.suspend-timeout-seconds=0",  # never auto-suspend
            "-",
        ]
        self._capture_proc = subprocess.Popen(
            capture_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=pw_env,
        )

        # Start playback: pw-cat --playback to create processed node
        playback_cmd = [
            "pw-cat", "--playback",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--latency", str(BUFFER_SAMPLES),  # 480 samples = 10ms
            "-P", f"application.name={PLAYBACK_TAG}",
            "-P", f"node.name={PLAYBACK_TAG}",
            "-P", "media.class=Audio/Source",
            "-P", "device.description=PulseForge-Mic",
            "-P", "node.description=PulseForge-Mic",
            "-",
        ]
        self._playback_proc = subprocess.Popen(
            playback_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=pw_env,
        )

        self._write_wav_header()

        # Wait for processes to initialize (reduced from 1.0s — pw-cat starts fast)
        time.sleep(0.3)

        # Check if pw-cat processes survived startup
        if self._capture_proc.poll() is not None:
            err = self._capture_proc.stderr.read().decode(errors='replace')
            print(f"  NativeMicChain: CAPTURE pw-cat died! stderr: {err}")
            # Retry once
            self._capture_proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--format", "f32",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", str(CHANNELS),
                    "-P", f"application.name={CAPTURE_TAG}",
                    "-P", f"node.name={CAPTURE_TAG}",
                    "-P", "session.suspend-timeout-seconds=0",  # never auto-suspend
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            time.sleep(1.0)
            if self._capture_proc.poll() is not None:
                err = self._capture_proc.stderr.read().decode(errors='replace')
                print(f"  NativeMicChain: capture retry failed: {err}")
                return
            print("  NativeMicChain: capture retry succeeded")

        if self._playback_proc.poll() is not None:
            print("  NativeMicChain: PLAYBACK pw-cat died during startup!")
            return

        # Redirect capture to the hardware mic input
        self._redirect_capture()

        # Delayed redirect — reconnect to ensure capture is active
        def _delayed_kick():
            time.sleep(0.5)
            if self._running:
                self._redirect_capture()
                print("  NativeMicChain: delayed redirect applied")
        threading.Thread(target=_delayed_kick, daemon=True).start()

        # Start processing thread
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

        print("  NativeMicChain: started (capture → process → playback)")

    def stop(self):
        """Stop the chain."""
        self._running = False

        # Wait for processing thread to stop BEFORE destroying C state
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

        if self._capture_proc:
            try:
                # Explicitly close pipes to prevent FD leak
                for pipe in (self._capture_proc.stdout, self._capture_proc.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                self._capture_proc.terminate()
                self._capture_proc.wait(timeout=2)
            except Exception:
                try:
                    self._capture_proc.kill()
                except Exception:
                    pass
            self._capture_proc = None

        if self._playback_proc:
            try:
                # Explicitly close all pipes
                for pipe in (self._playback_proc.stdin, self._playback_proc.stdout, self._playback_proc.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                self._playback_proc.terminate()
                self._playback_proc.wait(timeout=2)
            except Exception:
                try:
                    self._playback_proc.kill()
                except Exception:
                    pass
            self._playback_proc = None

        # Destroy C state after thread is fully stopped
        try:
            self.noise.destroy()
        except Exception as e:
            print(f"  NativeMicChain: noise destroy error: {e}")

        print("  NativeMicChain: stopped")

    def _write_wav_header(self):
        """Write a WAV header to the playback stdin so pw-cat accepts the stream."""
        import struct
        sr = SAMPLE_RATE
        fake_size = sr * 3600 * CHANNELS * 4  # 1 hour of audio

        header = b'RIFF'
        header += struct.pack('<I', 36 + fake_size - 8)
        header += b'WAVE'
        header += b'fmt '
        header += struct.pack('<IHHIIHH', 16, 3, CHANNELS, sr, sr * CHANNELS * 4, CHANNELS, 32)
        header += b'data'
        header += struct.pack('<I', fake_size)

        self._playback_proc.stdin.write(header)
        self._playback_proc.stdin.flush()

    def _redirect_capture(self, target_source_name: str = None) -> bool:
        """Redirect the capture pw-cat to a hardware input source.

        Auto-detection priority when no target specified:
          1. Configured input device (from config)
          2. Any non-monitor, non-pulseforge hardware source
          3. First available source as last resort
        """
        try:
            r = subprocess.run(
                ["pactl", "list", "source-outputs"],
                capture_output=True, text=True, timeout=5
            )

            blocks = r.stdout.split("Source Output #")
            for block in blocks[1:]:
                idx = block.split("\n")[0].strip()
                if CAPTURE_TAG in block:
                    # Get available sources
                    r2 = subprocess.run(
                        ["pactl", "list", "short", "sources"],
                        capture_output=True, text=True, timeout=5
                    )
                    target = None
                    target_name = None

                    if target_source_name:
                        # User-specified device — find it exactly
                        for line in r2.stdout.split("\n"):
                            parts = line.split("\t")
                            if len(parts) >= 2 and parts[1] == target_source_name:
                                target = parts[0]
                                target_name = parts[1]
                                break
                    else:
                        # Auto-detect: prefer configured device, then any hardware input
                        configured = self._configured_input_device
                        candidates = []
                        for line in r2.stdout.split("\n"):
                            parts = line.split("\t")
                            if len(parts) < 2:
                                continue
                            name = parts[1]
                            # Skip monitors and pulseforge virtual nodes
                            if ".monitor" in name or "pulseforge" in name:
                                continue
                            # Skip virtual sources (app-created)
                            if "module-virtual-source" in name:
                                continue
                            candidates.append((parts[0], name))

                        # Try configured device first
                        if configured:
                            for sid, sname in candidates:
                                if sname == configured:
                                    target = sid
                                    target_name = sname
                                    break

                        # Fall back to first hardware input
                        if not target and candidates:
                            target = candidates[0][0]
                            target_name = candidates[0][1]

                    if target:
                        subprocess.run(
                            ["pactl", "move-source-output", idx, target],
                            capture_output=True, timeout=5
                        )
                        subprocess.run(
                            ["pactl", "set-source-volume", target_name or target, "100%"],
                            capture_output=True, timeout=5
                        )
                        print(f"  NativeMicChain: capture redirected to source #{target} ({target_name})")
                        return True
                    print(f"  NativeMicChain: no suitable input source found")
                    return False
            return False
        except Exception as e:
            print(f"  NativeMicChain: redirect failed: {e}")
            return False

    def redirect_capture(self, source_name: str = None):
        """Redirect capture to a different input source."""
        moved = self._redirect_capture(source_name)
        if moved:
            return

        print(f"  NativeMicChain: source-output not found, restarting capture")
        if self._capture_proc:
            try:
                # Explicitly close pipes before killing to prevent FD leak
                for pipe in (self._capture_proc.stdout, self._capture_proc.stderr):
                    if pipe:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                self._capture_proc.terminate()
                self._capture_proc.wait(timeout=2)
            except Exception:
                try:
                    self._capture_proc.kill()
                except Exception:
                    pass

            pw_env = {**__import__('os').environ, "PIPEWIRE_LATENCY": f"{BUFFER_SAMPLES}/{SAMPLE_RATE}"}
            self._capture_proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--format", "f32",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", str(CHANNELS),
                    "--latency", str(BUFFER_SAMPLES),
                    "-P", f"application.name={CAPTURE_TAG}",
                    "-P", f"node.name={CAPTURE_TAG}",
                    "-P", "session.suspend-timeout-seconds=0",  # never auto-suspend
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=pw_env,
            )
            time.sleep(0.3)
            self._redirect_capture(source_name)
            print(f"  NativeMicChain: capture restarted and redirected")

    def _resume_source(self):
        """Resume a suspended hardware source and re-redirect capture to it."""
        try:
            # Find the configured/current hardware source
            target_name = self._configured_input_device
            if not target_name:
                # Find any non-monitor, non-pulseforge hardware source
                r = subprocess.run(
                    ["pactl", "list", "short", "sources"],
                    capture_output=True, text=True, timeout=5
                )
                for line in r.stdout.split("\n"):
                    parts = line.split("\t")
                    if len(parts) >= 2 and ".monitor" not in parts[1] and "pulseforge" not in parts[1]:
                        target_name = parts[1]
                        break

            if not target_name:
                print("  NativeMicChain: no hardware source to resume")
                return

            # Resume the source by setting suspend to 0 (triggers state change)
            # pw-cli set-param can resume a suspended node
            # Or use pactl to suspend/resume
            subprocess.run(
                ["pactl", "suspend-source", target_name, "0"],
                capture_output=True, timeout=5
            )
            print(f"  NativeMicChain: resumed source {target_name}")

            # Also re-redirect capture to make sure pw-cat reconnects
            self._redirect_capture(target_name)
        except Exception as e:
            print(f"  NativeMicChain: resume failed: {e}")

    def _process_loop(self):
        """Main processing loop: read → process → write.
        Auto-restarts pw-cat processes if they die."""
        # Silence counter for source kick
        _silence_count = 0
        _kicked = False

        while self._running:
            try:
                # Check if capture is alive
                if self._capture_proc.poll() is not None:
                    err = self._capture_proc.stderr.read(4096).decode(errors='replace') if self._capture_proc.stderr else 'unknown'
                    print(f"  NativeMicChain: capture pw-cat died! stderr: {err}")
                    if not self._restart_capture():
                        break

                data = self._capture_proc.stdout.read(BUFFER_BYTES)
                if not data or len(data) < BUFFER_BYTES:
                    print("  NativeMicChain: capture stream ended, restarting...")
                    if not self._restart_capture():
                        break
                    continue

                samples = np.frombuffer(data[:BUFFER_BYTES], dtype=np.float32)
                block = samples.reshape(-1, CHANNELS)

                # Detect extended silence (source SUSPENDED) and kick it
                raw_peak = float(np.max(np.abs(block)))
                if raw_peak < 1e-7:
                    _silence_count += 1
                    if _silence_count >= 30 and not _kicked:  # ~3 seconds of silence
                        print("  NativeMicChain: source appears suspended, resuming...")
                        self._resume_source()
                        _kicked = True
                else:
                    _silence_count = 0
                    _kicked = False

                # For mono (CHANNELS=1), squeeze to 1D so processors use mono path
                if CHANNELS == 1:
                    block = block.squeeze()  # (480,) 1D array

                # Process: gate → AFX → EQ → compressor
                block = self.gate.process(block)
                if not np.all(np.isfinite(block)):
                    block = np.zeros_like(block)

                block = self.noise.process(block)
                if not np.all(np.isfinite(block)):
                    block = np.zeros_like(block)

                block = self.eq.process(block)
                if not np.all(np.isfinite(block)):
                    block = np.zeros_like(block)

                block = self.compressor.process(block)
                if not np.all(np.isfinite(block)):
                    block = np.zeros_like(block)

                # Update peak level for VU
                peak = float(np.max(np.abs(block)))
                with self._peak_lock:
                    self._peak = peak

                # Accumulate samples for spectrum analyzer (mono — block is already mono)
                mono = block[:, 0] if block.ndim > 1 else block
                n = len(mono)
                end = self._spec_write_pos + n
                if end <= FFT_SIZE:
                    self._spec_buffer[self._spec_write_pos:end] = mono
                else:
                    first = FFT_SIZE - self._spec_write_pos
                    self._spec_buffer[self._spec_write_pos:] = mono[:first]
                    self._spec_buffer[:end - FFT_SIZE] = mono[first:]
                self._spec_write_pos = end % FFT_SIZE

                # Compute spectrum every HOP_SIZE samples
                self._spec_hop_counter += n
                if self._spec_hop_counter >= HOP_SIZE:
                    self._spec_hop_counter = 0
                    # Roll ring buffer into pre-allocated array (avoids np.roll allocation)
                    wpos = self._spec_write_pos
                    self._spec_rolled[:FFT_SIZE - wpos] = self._spec_buffer[wpos:]
                    if wpos > 0:
                        self._spec_rolled[FFT_SIZE - wpos:] = self._spec_buffer[:wpos]
                    spectrum = _compute_spectrum(self._spec_rolled, FFT_SIZE, NUM_SPEC_POINTS)
                    with self._spec_lock:
                        self._spectrum = spectrum

                # Write to playback (stdin) — non-blocking with timeout
                try:
                    if self._playback_proc.poll() is not None:
                        err = self._playback_proc.stderr.read(4096).decode(errors='replace') if self._playback_proc.stderr else 'unknown'
                        print(f"  NativeMicChain: playback pw-cat died! stderr: {err}")
                        if not self._restart_playback():
                            break
                    # Use os.write for non-blocking capability
                    import os
                    data_bytes = block.tobytes()
                    fd = self._playback_proc.stdin.fileno()
                    # Set non-blocking, write, flush remaining, set back to blocking
                    import fcntl
                    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    try:
                        os.write(fd, data_bytes)
                    except BlockingIOError:
                        # Buffer full — skip this block rather than stalling the chain
                        _stall_count = getattr(self, '_stall_count', 0) + 1
                        if _stall_count % 50 == 1:  # Log occasionally
                            print(f"  NativeMicChain: playback buffer full, dropping block ({_stall_count})")
                        self._stall_count = _stall_count
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags)  # restore original flags
                except (BrokenPipeError, IOError, OSError):
                    print("  NativeMicChain: playback pipe broke, restarting...")
                    if not self._restart_playback():
                        break

            except Exception as e:
                print(f"  NativeMicChain: error: {e}")
                time.sleep(0.1)

    # ─── Real-time parameter updates ──────────────

    def _restart_capture(self) -> bool:
        """Restart the capture pw-cat process. Returns True on success."""
        print("  NativeMicChain: restarting capture...")
        try:
            if self._capture_proc:
                try:
                    # Explicitly close pipes to prevent FD leak
                    for pipe in (self._capture_proc.stdout, self._capture_proc.stderr):
                        if pipe:
                            try:
                                pipe.close()
                            except Exception:
                                pass
                    self._capture_proc.kill()
                    self._capture_proc.wait(timeout=1)
                except Exception:
                    pass
            pw_env = {**__import__('os').environ, "PIPEWIRE_LATENCY": f"{BUFFER_SAMPLES}/{SAMPLE_RATE}"}
            self._capture_proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--format", "f32",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", str(CHANNELS),
                    "--latency", str(BUFFER_SAMPLES),
                    "-P", f"application.name={CAPTURE_TAG}",
                    "-P", f"node.name={CAPTURE_TAG}",
                    "-P", "session.suspend-timeout-seconds=0",  # never auto-suspend
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=pw_env,
            )
            time.sleep(0.3)
            if self._capture_proc.poll() is not None:
                err = self._capture_proc.stderr.read(4096).decode(errors='replace') if self._capture_proc.stderr else 'unknown'
                print(f"  NativeMicChain: capture restart failed: {err}")
                return False
            self._redirect_capture()
            print("  NativeMicChain: capture restarted")
            return True
        except Exception as e:
            print(f"  NativeMicChain: capture restart error: {e}")
            return False

    def _restart_playback(self) -> bool:
        """Restart the playback pw-cat process. Returns True on success."""
        print("  NativeMicChain: restarting playback...")
        try:
            if self._playback_proc:
                try:
                    # Explicitly close all pipes to prevent FD leak
                    for pipe in (self._playback_proc.stdin, self._playback_proc.stdout, self._playback_proc.stderr):
                        if pipe:
                            try:
                                pipe.close()
                            except Exception:
                                pass
                    self._playback_proc.kill()
                    self._playback_proc.wait(timeout=1)
                except Exception:
                    pass
                self._playback_proc = None
            pw_env = {**__import__('os').environ, "PIPEWIRE_LATENCY": f"{BUFFER_SAMPLES}/{SAMPLE_RATE}"}
            self._playback_proc = subprocess.Popen(
                [
                    "pw-cat", "--playback",
                    "--rate", str(SAMPLE_RATE),
                    "--channels", str(CHANNELS),
                    "--latency", str(BUFFER_SAMPLES),
                    "-P", f"application.name={PLAYBACK_TAG}",
                    "-P", f"node.name={PLAYBACK_TAG}",
                    "-P", "media.class=Audio/Source",
                    "-P", "device.description=PulseForge-Mic",
                    "-P", "node.description=PulseForge-Mic",
                    "-",
                ],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=pw_env,
            )
            self._write_wav_header()
            time.sleep(0.3)
            if self._playback_proc is None or self._playback_proc.poll() is not None:
                err = self._playback_proc.stderr.read(4096).decode(errors='replace') if self._playback_proc and self._playback_proc.stderr else 'unknown'
                print(f"  NativeMicChain: playback restart failed: {err}")
                return False
            print("  NativeMicChain: playback restarted")
            return True
        except Exception as e:
            print(f"  NativeMicChain: playback restart error: {e}")
            return False

    def set_gate(self, threshold_db: float = None, enabled: bool = None,
                 attack_ms: float = None, hold_ms: float = None,
                 release_ms: float = None, range_db: float = None):
        if threshold_db is not None:
            self.gate.set_threshold(threshold_db)
        if enabled is not None:
            self.gate.set_enabled(enabled)
        if attack_ms is not None:
            self.gate.set_attack(attack_ms)
        if hold_ms is not None:
            self.gate.set_hold(hold_ms)
        if release_ms is not None:
            self.gate.set_release(release_ms)
        if range_db is not None:
            self.gate.set_range(range_db)

    def set_eq_band(self, idx: int, freq: float, gain_db: float, q: float):
        self.eq.set_band(idx, freq, gain_db, q)

    def set_eq_enabled(self, enabled: bool):
        self.eq.set_enabled(enabled)

    def set_eq_bands(self, bands: list[dict], enabled: bool = None):
        if enabled is not None:
            self.eq.set_enabled(enabled)
        for i, b in enumerate(bands[:8]):
            self.eq.set_band(i, b['freq'], b['gain'], b['q'])

    def set_noise(self, intensity: float = None, enabled: bool = None):
        if intensity is not None:
            # AFX uses 0.0-1.0 intensity scale
            self.noise.set_intensity(max(0.0, min(1.0, intensity / 100.0)))
        if enabled is not None:
            self.noise.set_enabled(enabled)

    def set_input_device(self, source_name: str = None):
        """Set the configured input device and redirect capture to it."""
        self._configured_input_device = source_name
        if source_name:
            self.redirect_capture(source_name)

    def set_compressor(self, threshold_db: float = None, ratio: float = None,
                       attack_ms: float = None, release_ms: float = None,
                       makeup_db: float = None, enabled: bool = None):
        self.compressor.set_params(
            threshold_db=threshold_db,
            ratio=ratio,
            attack_ms=attack_ms,
            release_ms=release_ms,
            makeup_db=makeup_db,
            enabled=enabled,
        )

    def get_peak_vu(self) -> float:
        """Get current peak level as VU value (0.0-1.0)."""
        from .vu_meter import _linear_to_vu
        with self._peak_lock:
            peak = self._peak
            self._peak = peak * 0.95
        return _linear_to_vu(peak)

    def get_spectrum(self) -> list:
        """Get current spectrum data (list of {freq, level} dicts)."""
        with self._spec_lock:
            return list(self._spectrum) if self._spectrum else []


# Global singleton
_chain: Optional[NativeMicChain] = None


def get_chain() -> NativeMicChain:
    global _chain
    if _chain is None:
        _chain = NativeMicChain()
    return _chain
