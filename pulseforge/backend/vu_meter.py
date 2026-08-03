"""VU meter — peak level monitoring for PipeWire nodes.

Uses pw-cat --record with a unique application.name tag, then
pactl move-source-output to redirect to the correct monitor source.

Processes are tracked by the ProcessManager under category "vu:<sink>".
"""
import subprocess
import struct
import threading
import time
import math
import os
import signal
from collections import defaultdict
from typing import Callable

from . import pipewire_ctl as pw
from .process_manager import get_manager


def _linear_to_vu(peak: float) -> float:
    """Convert raw linear peak (0.0-1.0+) to VU display value (0.0-1.0).

    Maps -72dB..0dB → 0.0..1.0 for visual display.
    """
    if peak <= 0.0 or not math.isfinite(peak):
        return 0.0
    db = 20.0 * math.log10(min(peak, 1.0))
    vu = (db + 72.0) / 72.0
    return max(0.0, min(1.0, vu))


class MonitorProcess:
    """A pw-cat monitor process for one sink, redirected via pactl move-source-output.

    Uses the ProcessManager for lifecycle tracking.
    """

    def __init__(self, sink_name: str):
        self._sink_name = sink_name  # e.g. "pulseforge_chat"
        self._monitor_source = f"{sink_name}.monitor"
        self._app_tag = f"pulseforge_vu_{sink_name}"
        self._peak = 0.0
        self._running = False
        self._thread = None
        self._proc = None  # raw Popen for stdout reading

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._proc:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=2)

    def get_peak(self) -> float:
        return self._peak

    def _run(self):
        """Run pw-cat, redirect it to the monitor source, and read peak levels."""
        try:
            # Start pw-cat directly — we need stdout=PIPE to read audio
            self._proc = subprocess.Popen(
                [
                    "pw-cat", "--record",
                    "--format", "f32",
                    "--rate", "48000",
                    "--channels", "1",
                    "-P", f"application.name={self._app_tag}",
                    "-P", f"node.name={self._app_tag}",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            print(f"  VU: Started monitor for {self._sink_name} (pid={self._proc.pid})")

            # Wait for pw-cat to create its source-output
            time.sleep(1.0)

            # Redirect to monitor source
            redirected = self._redirect_to_monitor()
            if not redirected:
                for _ in range(3):
                    time.sleep(0.5)
                    if self._redirect_to_monitor():
                        redirected = True
                        break
                if not redirected:
                    print(f"  VU: Failed to redirect monitor for {self._sink_name}")

            # Read audio data (runs until process exits)
            self._read_audio()

        except Exception as e:
            print(f"  VU: Monitor error for {self._sink_name}: {e}")
        finally:
            self._running = False

    def _redirect_to_monitor(self) -> bool:
        """Find our pw-cat by tag and redirect it to the monitor source."""
        try:
            r = subprocess.run(
                ["pactl", "list", "source-outputs"],
                capture_output=True, text=True, timeout=5
            )

            blocks = r.stdout.split("Source Output #")
            for block in blocks[1:]:
                idx = block.split("\n")[0].strip()
                if self._app_tag in block:
                    r2 = subprocess.run(
                        ["pactl", "move-source-output", idx, self._monitor_source],
                        capture_output=True, text=True, timeout=5
                    )
                    return r2.returncode == 0
            return False
        except Exception:
            return False

    def _read_audio(self):
        """Read raw float32 audio from pw-cat stdout and calculate peak."""
        if self._proc is None:
            return

        chunk_size = 1024 * 4  # 1024 float32 samples

        while self._running and self._proc.poll() is None:
            try:
                data = self._proc.stdout.read(chunk_size)
                if not data:
                    break
                count = len(data) // 4
                if count > 0:
                    samples = struct.unpack(f"{count}f", data[:count * 4])
                    peak = max(abs(s) for s in samples) if samples else 0.0
                    self._peak = _linear_to_vu(peak)
            except Exception:
                break

    def _fallback_loop(self):
        """Fallback: use wpctl get-volume (reports set volume, not peak)."""
        node_id = pw.get_node_by_name(self._sink_name)
        if node_id is None:
            return

        while self._running:
            try:
                vol = pw.get_volume(node_id)
                muted = pw.is_muted(node_id)
                self._peak = 0.0 if muted else vol
            except Exception:
                pass
            time.sleep(0.1)


class VUMeterPoller:
    """Polls PipeWire for peak levels and calls callbacks at ~30fps."""

    def __init__(self):
        self._callbacks: dict[int, list[Callable[[float], None]]] = defaultdict(list)
        self._monitors: dict[int, MonitorProcess] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._interval = 0.033  # ~30fps
        self._start_lock = threading.Lock()

    def add_callback(self, node_id: int, callback: Callable[[float], None]):
        self._callbacks[node_id].append(callback)
        if node_id not in self._monitors:
            self._start_monitor(node_id)

    def remove_callback(self, node_id: int, callback: Callable[[float], None]):
        if node_id in self._callbacks:
            try:
                self._callbacks[node_id].remove(callback)
            except ValueError:
                pass
            if not self._callbacks[node_id]:
                del self._callbacks[node_id]
                if node_id in self._monitors:
                    self._monitors[node_id].stop()
                    del self._monitors[node_id]

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        for monitor in self._monitors.values():
            monitor.stop()
        self._monitors.clear()
        if self._thread:
            self._thread.join(timeout=2)

    def _start_monitor(self, node_id: int):
        """Start a monitor process for a sink node (non-blocking)."""
        def _start():
            with self._start_lock:
                sink_name = pw.get_sink_name_for_node(node_id)
                if not sink_name:
                    return

                monitor = MonitorProcess(sink_name)
                monitor.start()
                self._monitors[node_id] = monitor

        # Start in background thread so we don't block the poll loop
        t = threading.Thread(target=_start, daemon=True)
        t.start()

    def _poll_loop(self):
        """Dispatch peak values to callbacks at ~30fps."""
        while self._running:
            for node_id, monitor in list(self._monitors.items()):
                peak = monitor.get_peak()
                if node_id in self._callbacks:
                    for cb in self._callbacks[node_id]:
                        try:
                            cb(peak)
                        except Exception:
                            pass
            time.sleep(self._interval)


# Global instance
_poller: VUMeterPoller | None = None


def get_poller() -> VUMeterPoller:
    global _poller
    if _poller is None:
        _poller = VUMeterPoller()
    return _poller
