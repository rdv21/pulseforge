"""Process manager for PulseForge spawned processes.

Tracks all subprocess.Popen instances by category, provides clean
restart/stop for individual processes, and cleans up on exit.

Categories:
  - "gate"        — Gate filter-chain process
  - "eq"          — EQ filter-chain process
  - "rnnoise"     — RNNoise filter-chain process
  - "compressor"  — Compressor filter-chain process
  - "vu:<sink>"   — VU meter pw-cat monitor for a sink

Each filter-chain process creates a uniquely named node so we can
find it via pw-cli even if the Popen handle is lost (e.g. after crash).
"""
import subprocess
import os
import signal
import time
import threading
from pathlib import Path
from typing import Optional


class ManagedProcess:
    """A tracked subprocess with metadata."""
    def __init__(self, proc: subprocess.Popen, category: str, node_name: str = ""):
        self.proc = proc
        self.category = category
        self.node_name = node_name  # PipeWire node.name this process creates
        self.started_at = time.time()
        self._lock = threading.Lock()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def is_running(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop the process gracefully (SIGTERM → SIGKILL)."""
        with self._lock:
            if not self.is_running:
                return True
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass

            try:
                self.proc.wait(timeout=timeout)
                return True
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
                try:
                    self.proc.wait(timeout=1)
                except Exception:
                    pass
                return False

    def restart(self, cmd: list[str], timeout: float = 3.0) -> bool:
        """Stop and re-launch with a new command."""
        self.stop(timeout)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.started_at = time.time()
            return True
        except Exception:
            return False


class ProcessManager:
    """Centralized manager for all PulseForge spawned processes."""

    def __init__(self):
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()

    def start(self, category: str, cmd: list[str], node_name: str = "",
              start_new_session: bool = True) -> Optional[int]:
        """Start a process and track it under `category`.

        Returns the PID, or None on failure.
        """
        with self._lock:
            # Stop existing process in this category
            if category in self._processes:
                self._processes[category].stop(timeout=2.0)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=start_new_session,
                )
            except Exception as e:
                print(f"  PM: Failed to start '{category}': {e}")
                return None

            managed = ManagedProcess(proc, category, node_name)
            self._processes[category] = managed
            print(f"  PM: Started '{category}' (pid={proc.pid}, node={node_name})")
            return proc.pid

    def stop(self, category: str, timeout: float = 3.0) -> bool:
        """Stop a specific process by category."""
        with self._lock:
            mp = self._processes.get(category)
            if mp is None:
                return True
            result = mp.stop(timeout)
            if result:
                del self._processes[category]
                print(f"  PM: Stopped '{category}'")
            return result

    def restart(self, category: str, cmd: list[str] = None, timeout: float = 3.0) -> bool:
        """Restart a process. If cmd is None, uses the stored command.

        For filter-chain processes, cmd should be provided since we don't store it.
        """
        with self._lock:
            mp = self._processes.get(category)
            if mp is None:
                print(f"  PM: Cannot restart '{category}' — not running")
                return False

            if cmd is None:
                # Can't restart without a command
                return False

            return mp.restart(cmd, timeout)

    def is_running(self, category: str) -> bool:
        """Check if a process is running."""
        with self._lock:
            mp = self._processes.get(category)
            return mp is not None and mp.is_running

    def get_pid(self, category: str) -> Optional[int]:
        """Get the PID of a tracked process."""
        with self._lock:
            mp = self._processes.get(category)
            if mp and mp.is_running:
                return mp.pid
            return None

    def get_node_name(self, category: str) -> str:
        """Get the PipeWire node name for a category."""
        with self._lock:
            mp = self._processes.get(category)
            return mp.node_name if mp else ""

    def list_categories(self) -> list[str]:
        """List all tracked categories."""
        with self._lock:
            return list(self._processes.keys())

    def list_running(self) -> list[dict]:
        """List all running processes with details."""
        with self._lock:
            result = []
            for cat, mp in self._processes.items():
                if mp.is_running:
                    result.append({
                        "category": cat,
                        "pid": mp.pid,
                        "node_name": mp.node_name,
                        "uptime": time.time() - mp.started_at,
                    })
            return result

    def stop_all(self, timeout: float = 3.0):
        """Stop all tracked processes. Used for clean shutdown."""
        with self._lock:
            for category in list(self._processes.keys()):
                mp = self._processes[category]
                mp.stop(timeout)
            self._processes.clear()
            print("  PM: All processes stopped")

    def cleanup_orphans(self):
        """Kill orphaned processes matching PulseForge patterns.

        Uses pgrep to find processes that should be tracked but aren't
        (e.g. from a previous crashed instance).
        """
        patterns = [
            ("pw-cat.*pulseforge", "pw-cat"),
            ("pipewire -c filter-chain", "filter-chain"),
        ]

        for pattern, label in patterns:
            try:
                r = subprocess.run(
                    ["pgrep", "-f", pattern],
                    capture_output=True, text=True, timeout=2
                )
                pids = [int(p) for p in r.stdout.strip().split('\n') if p.strip()]
                tracked_pids = {mp.pid for mp in self._processes.values() if mp.is_running}

                for pid in pids:
                    if pid not in tracked_pids:
                        print(f"  PM: Killing orphaned {label} process {pid}")
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            pass

                # Wait and SIGKILL survivors
                time.sleep(0.5)
                for pid in pids:
                    if pid not in tracked_pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except Exception:
                            pass
            except Exception:
                pass


# Global singleton
_manager: Optional[ProcessManager] = None


def get_manager() -> ProcessManager:
    global _manager
    if _manager is None:
        _manager = ProcessManager()
    return _manager
