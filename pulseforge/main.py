#!/usr/bin/env python3
"""PulseForge — Main entry point (QML UI + native mic chain).

Architecture:
  - QML frontend (Qt Quick) for UI
  - Python backend (PipeWire control + native DSP mic chain)
  - Real-time parameter updates (no process restarts for mic params)

Run: python3 -m pulseforge.main
"""
import sys
import os
import math
import time
import struct
import subprocess
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Signal, Slot, Property, QTimer, QThread, QSize
from PySide6.QtGui import QGuiApplication, QIcon, QAction, QPixmap
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtQml import QQmlApplicationEngine, QQmlImageProviderBase
from PySide6.QtQuick import QQuickImageProvider

from .backend import config
from .backend import pipewire_ctl as pw
from .backend import app_router
from .backend.dsp import AFXNoiseProcessor
from .backend.native_chain import NativeMicChain
from .backend.vu_meter import get_poller
from .backend.process_manager import get_manager
from .backend.soundboard import SoundboardBackend


# ─── QML Bridge ────────────────────────────────────────────────────
# This object is exposed to QML as a context property.
# QML calls slots directly; Python pushes updates via signals.

class PulseForgeBridge(QObject):
    """Bridge between QML UI and Python backend."""

    # ─── Signals (push updates to QML) ───
    vuUpdated = Signal(float, float, arguments=['mainVu', 'streamVu'])  # master VU
    channelVuUpdated = Signal(str, float, float, arguments=['channel', 'mainVu', 'streamVu'])
    micVuUpdated = Signal(float)
    micSpectrumUpdated = Signal('QVariant', arguments=['spectrum'])
    streamVuUpdated = Signal(float)
    appsChanged = Signal()
    devicesChanged = Signal()
    faderSynced = Signal(str, float, arguments=['channel', 'volume'])
    statusMessage = Signal(str, arguments=['message'])  # toast/status bar
    errorOccurred = Signal(str, arguments=['message'])  # error toast
    soundboardChanged = Signal(int, arguments=['page'])  # grid slots changed, reload page

    def __init__(self):
        super().__init__()
        self._config = config.load_config()
        self._routing = config.load_routing()
        self._mic_chain = NativeMicChain()
        self._vu_poller = get_poller()
        self._group_sink_ids: dict = {}
        self._mic_processed_node: Optional[int] = None

        # VU timer — pushes updates to QML at 30fps
        self._vu_timer = QTimer(self)
        self._vu_timer.setInterval(33)
        self._vu_timer.timeout.connect(self._push_vu)

        # App refresh timer
        self._app_timer = QTimer(self)
        self._app_timer.setInterval(10000)
        self._app_timer.timeout.connect(self._refresh_apps)

        # Routing check timer
        self._route_timer = QTimer(self)
        self._route_timer.setInterval(3000)
        self._route_timer.timeout.connect(self._refresh_routing)

        # Fader sync timer — reads back loopback volumes to sync UI
        self._fader_sync_timer = QTimer(self)
        self._fader_sync_timer.setInterval(2000)
        self._fader_sync_timer.timeout.connect(self._sync_faders)

        # Device polling timer — detects hotplug/unplug, stale loopbacks, vrserver lifecycle
        self._device_timer = QTimer(self)
        self._device_timer.setInterval(5000)
        self._device_timer.timeout.connect(self._check_devices)

        # vrserver lifecycle tracking
        self._vrserver_was_running = False
        self._vrserver_audio_snapshot: Optional[dict] = None
        self._vrserver_recovering = False

        # Soundboard
        config_dir = Path.home() / ".config" / "pulseforge"
        self._soundboard = SoundboardBackend(config_dir)

    # ─── Lifecycle ───

    def start(self):
        """Called after QML is loaded. Sets up audio graph and starts chains."""
        self._setup_audio_graph()
        self._start_mic_chain()
        self._restore_mic_stream()
        self._setup_vu_meters()
        self._refresh_devices()
        self._refresh_apps()
        self._start_soundboard_recording()

        self._vu_timer.start()
        self._app_timer.start()
        self._route_timer.start()
        self._fader_sync_timer.start()
        self._device_timer.start()

        self.statusMessage.emit("PulseForge ready")

    def _start_soundboard_recording(self):
        """Start always-on recording for all channels."""
        from .backend import pipewire_ctl as pw
        monitor_map = {}
        for channel in ["game", "chat", "media", "aux"]:
            internal = pw._VIRTUAL_SINK_INTERNAL.get(channel, f"pulseforge_{channel}")
            monitor_map[channel] = f"{internal}.monitor"
        # Mic uses the processed mic source
        monitor_map["mic"] = "pulseforge.mic.processed"
        self._soundboard.start_all_recording(monitor_map)

    def stop(self):
        """Clean shutdown — stop everything and remove PipeWire objects."""
        self._vu_timer.stop()
        self._app_timer.stop()
        self._route_timer.stop()
        self._fader_sync_timer.stop()
        self._device_timer.stop()
        self._vu_poller.stop()
        self._mic_chain.stop()
        get_manager().stop_all()

        # Kill any remaining pw-cat processes (graceful via ProcessManager)
        get_manager().cleanup_orphans()

        # Stop soundboard (recording + playback)
        self._cleanup_soundboard()

        # Remove all PulseForge PipeWire objects
        print("  Shutdown: cleaning up PulseForge objects...")
        try:
            pw.remove_all_pulseforge_loopbacks()
            pw.remove_all_pulseforge_virtual_sources()
            pw.destroy_all_pulseforge_sources()
            pw.destroy_all_pulseforge_sinks()
        except Exception as e:
            print(f"  Shutdown: error during cleanup: {e}")
        print("  Shutdown: done.")

    # ─── Audio Graph Setup ───

    def _setup_audio_graph(self):
        """Create virtual sinks, loopbacks, set defaults."""
        # ─── Clean slate: remove ALL leftover PulseForge objects ───
        print("  Cleanup: removing leftover PulseForge objects...")
        # Kill leftover pw-cat processes first (frees source/sink nodes)
        get_manager().cleanup_orphans()
        time.sleep(0.5)
        # Remove all loopbacks
        pw.remove_all_pulseforge_loopbacks()
        # Remove virtual source modules (stream_input)
        pw.remove_all_pulseforge_virtual_sources()
        # Destroy virtual sources (mic.processed etc)
        pw.destroy_all_pulseforge_sources()
        # Destroy virtual sinks
        pw.destroy_all_pulseforge_sinks()

        # Wait for PipeWire to fully deregister destroyed nodes.
        # The cleanup uses pactl unload-module / pw-cli destroy, but PipeWire
        # keeps stale nodes in pw-cli list-objects for a few seconds.
        # If we don't wait, create_virtual_sink sees the stale node and skips.
        max_wait = 5.0
        waited = 0.0
        while waited < max_wait:
            stale = False
            for group in pw.VIRTUAL_SINK_NAMES:
                internal = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
                if pw.get_node_by_name(internal) is not None:
                    stale = True
                    break
            if not stale:
                break
            time.sleep(0.5)
            waited += 0.5
        if stale:
            print(f"  Cleanup: WARNING - stale nodes still present after {max_wait}s wait")

        # ─── Create fresh virtual sinks ───
        self._group_sink_ids = pw.create_all_virtual_sinks()

        # Retry any sinks that failed to create (timing issue with PipeWire cleanup)
        for group, node_id in list(self._group_sink_ids.items()):
            if node_id is None:
                time.sleep(0.3)
                from .backend.pipewire_ctl import create_virtual_sink
                node_id = create_virtual_sink(group)
                self._group_sink_ids[group] = node_id

        for group, node_id in self._group_sink_ids.items():
            if node_id is not None:
                print(f"  Virtual sink '{group}': node {node_id}")
            else:
                print(f"  WARNING: Virtual sink '{group}' failed to create!")
                self.errorOccurred.emit(f"Failed to create {group} audio channel")

        gaming_id = self._group_sink_ids.get("gaming")
        if gaming_id is None:
            print("  WARNING: Gaming sink not created")
            self.errorOccurred.emit("Failed to create master audio sink. Audio routing will not work.")
            return

        pw.set_default_sink(gaming_id)
        pw.set_volume(gaming_id, 1.0)

        # Set stream sink volume from config
        stream_id = self._group_sink_ids.get("stream")
        stream_cfg = self._config.get("stream", {})
        if stream_id:
            pw.set_volume(stream_id, min(stream_cfg.get("volume", 1.0), 1.0))
            pw.set_mute(stream_id, stream_cfg.get("muted", False))

        # Create virtual Audio/Source from stream mix (for OBS/streaming apps)
        pw.create_stream_virtual_source()

        # Create loopbacks: game/chat/media/aux → gaming
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        existing_loopbacks = pw.list_loopbacks()
        self._channel_loopback_mods = {}  # channel → loopback module id
        for group in ["game", "chat", "media", "aux"]:
            group_internal = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
            monitor = pw.get_sink_monitor_source(group_internal)
            if monitor:
                exists = any(src == monitor and sink == gaming_internal for _, src, sink in existing_loopbacks)
                if not exists:
                    mod_idx = pw.create_loopback(monitor, gaming_internal,
                                                      initial_volume=0.0, ramp_to=1.0, ramp_ms=150)
                    if mod_idx is not None:
                        print(f"  Loopback: {monitor} → {gaming_internal}")
                else:
                    # Find existing module id
                    for idx, src, sink in existing_loopbacks:
                        if src == monitor and sink == gaming_internal:
                            mod_idx = idx
                            break
                self._channel_loopback_mods[group] = mod_idx
                # Restore volume on the loopback sink-input
                saved_vol = self._config.get("groups", {}).get(group, {}).get("output_volume", 1.0)
                self._set_loopback_volume(mod_idx, saved_vol)

        # Gaming → hardware output
        dev_cfg = self._config.get("devices", {})
        if dev_cfg.get("output"):
            self._create_gaming_to_hw_loopback(dev_cfg["output"])
        else:
            sinks = pw.list_sinks()
            hw_sink = None
            for s in sinks:
                if s.is_default and not s.name.startswith("pulseforge"):
                    hw_sink = s.name
                    break
            if not hw_sink:
                for s in sinks:
                    if not s.name.startswith("pulseforge"):
                        hw_sink = s.name
                        break
            if hw_sink:
                self._create_gaming_to_hw_loopback(hw_sink)
                self._config["devices"]["output"] = hw_sink
                config.save_config(self._config)

        # Restore stream loopbacks for channels with stream_enabled
        stream_sink = pw._VIRTUAL_SINK_INTERNAL.get("stream", "pulseforge_stream")
        existing_loopbacks = pw.list_loopbacks()
        for group in ["game", "chat", "media", "aux"]:
            group_cfg = self._config.get("groups", {}).get(group, {})
            if group_cfg.get("stream_enabled", False):
                group_internal = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
                group_monitor = pw.get_sink_monitor_source(group_internal) or f"{group_internal}.monitor"
                already = any(src == group_monitor and sink == stream_sink for _, src, sink in existing_loopbacks)
                if not already:
                    stream_vol = group_cfg.get("stream_volume", 1.0)
                    mod_idx = pw.create_loopback(group_monitor, stream_sink,
                                                      initial_volume=0.0, ramp_to=stream_vol, ramp_ms=150)
                    if mod_idx is not None:
                        self._set_stream_loopback_volume(group, mod_idx, stream_vol)
                        print(f"  Stream loopback: {group} → {stream_sink} (vol: {stream_vol:.2f})")

    def _create_gaming_to_hw_loopback(self, hw_sink_name: str):
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        gaming_monitor = pw.get_sink_monitor_source(gaming_internal) or f"{gaming_internal}.monitor"
        existing = pw.list_loopbacks()
        for _, src, sink in existing:
            if src == gaming_monitor and sink == hw_sink_name:
                return
        pw.create_loopback(gaming_monitor, hw_sink_name,
                            initial_volume=0.0, ramp_to=1.0, ramp_ms=150)

    def _restore_mic_stream(self):
        """Restore mic → stream and mic → monitor loopbacks if enabled in config.
        Retries for up to 3s since pulseforge.mic.processed takes a moment to appear.
        """
        mic_source = "pulseforge.mic.processed"

        def _wait_and_create(source_name, sink_name, label):
            """Retry loopback creation until the source node exists."""
            import threading
            def _try():
                for attempt in range(15):  # 3s total, 200ms intervals
                    existing = pw.list_loopbacks()
                    already = any(src == source_name and sink == sink_name for _, src, sink in existing)
                    if already:
                        return
                    # Check if source exists
                    sources = pw.list_sources()
                    if any(s.name == source_name for s in sources):
                        pw.create_loopback(source_name, sink_name,
                                            initial_volume=0.0, ramp_to=1.0, ramp_ms=150)
                        print(f"  {label}: {source_name} → {sink_name} (attempt {attempt+1})")
                        return
                    time.sleep(0.2)
                print(f"  {label}: source {source_name} never appeared, giving up")
            threading.Thread(target=_try, daemon=True).start()

        # Restore mic → stream
        if self._config.get("mic", {}).get("stream_enabled", False):
            _wait_and_create(mic_source, "pulseforge_stream", "Mic stream restored")

        # Restore mic → monitor (gaming)
        if self._config.get("mic", {}).get("monitor", False):
            _wait_and_create(mic_source, "pulseforge_gaming", "Mic monitor restored")

    def _start_mic_chain(self):
        """Start the native Python mic processing chain."""
        cfg = self._config.get("mic", {})

        # Apply saved settings to the chain before starting
        gate_cfg = cfg.get("gate", {})
        self._mic_chain.set_gate(
            threshold_db=gate_cfg.get("threshold", -50.0),
            enabled=gate_cfg.get("enabled", True),
            attack_ms=gate_cfg.get("attack", 25.0),
            hold_ms=gate_cfg.get("hold", 300.0),
            release_ms=gate_cfg.get("release", 200.0),
            range_db=gate_cfg.get("range", -25.0),
        )

        eq_cfg = cfg.get("eq", {})
        eq_bands = eq_cfg.get("bands", [])
        if eq_bands:
            self._mic_chain.set_eq_bands(eq_bands, eq_cfg.get("enabled", True))

        # Apply AFX settings
        afx_cfg = cfg.get("afx", {})
        afx = self._mic_chain._afx
        afx.set_intensity(afx_cfg.get("intensity", 0.7))
        afx.set_enabled(afx_cfg.get("enabled", True))

        comp_cfg = cfg.get("compressor", {})
        self._mic_chain.set_compressor(
            threshold_db=comp_cfg.get("threshold", -20.0),
            ratio=comp_cfg.get("amount", 3.0),
            makeup_db=comp_cfg.get("makeup", 0.0),
            enabled=comp_cfg.get("enabled", True),
        )

        self._mic_chain.start()

        if not self._mic_chain._running:
            self.errorOccurred.emit("Mic processing chain failed to start. Check that PipeWire is running and your input device is available.")

        # Set configured input device (used by auto-detection in _redirect_capture)
        saved_input = self._config.get("devices", {}).get("input", "")
        if saved_input:
            self._mic_chain.set_input_device(saved_input)
        else:
            self._mic_chain.redirect_capture()  # auto-detect any hardware input

        self._mic_processed_node = pw.get_node_by_name("pulseforge.mic.processed")

        # Set pulseforge.mic.processed as default source so apps capture from it
        if self._mic_processed_node is not None:
            pw.set_default_source(self._mic_processed_node)
            print(f"  Default source set to pulseforge.mic.processed (node {self._mic_processed_node})")

    def _setup_vu_meters(self):
        # Channel strip VU meters (main mix)
        for group, sink_id in self._group_sink_ids.items():
            if group == "gaming":
                continue
            self._vu_poller.add_callback(sink_id, lambda p, g=group: self._on_channel_vu(g, p))

        # Master VU (gaming sink = full mix)
        gaming_id = self._group_sink_ids.get("gaming")
        if gaming_id:
            self._vu_poller.add_callback(gaming_id, lambda p: self._on_master_vu(p))

        self._vu_poller.start()

    def _push_vu(self):
        """Push mic VU and spectrum to QML."""
        vu = self._mic_chain.get_peak_vu()
        self.micVuUpdated.emit(float(vu))
        # Push spectrum (every other frame to reduce load — ~15fps for spectrum)
        if not hasattr(self, '_spec_counter'):
            self._spec_counter = 0
        self._spec_counter += 1
        if self._spec_counter % 2 == 0:
            spectrum = self._mic_chain.get_spectrum()
            if spectrum:
                self.micSpectrumUpdated.emit(spectrum)

    def _on_master_vu(self, peak: float):
        self.vuUpdated.emit(float(peak), float(peak))

    def _on_channel_vu(self, channel: str, peak: float):
        # Main VU: what leaves the main mix (raw monitor × main fader)
        group_cfg = self._config.get("groups", {}).get(channel, {})
        main_vol = group_cfg.get("output_volume", 1.0)
        main_peak = peak * main_vol
        
        # Stream VU: post-stream-fader — what OBS/viewers actually capture
        if group_cfg.get("stream_enabled", False):
            stream_vol = group_cfg.get("stream_volume", 1.0)
            stream_peak = peak * stream_vol
        else:
            stream_peak = 0.0
        self.channelVuUpdated.emit(channel, float(main_peak), float(stream_peak))

    def _sync_faders(self):
        """Read back loopback sink-input volumes to sync UI faders with system state.
        Runs every 2s — catches changes made outside PulseForge (pactl, hardware knobs)."""
        try:
            r = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True, text=True, timeout=5
            )
            blocks = r.stdout.split("Sink Input #")
            for block in blocks[1:]:
                # Extract module id
                mod_match = re.search(r'pulse.module.id = "(\d+)"', block)
                if not mod_match:
                    continue
                mod_idx = int(mod_match.group(1))
                
                # Extract current volume
                vol_match = re.search(r'Volume:.*?(\d+)%', block)
                if not vol_match:
                    continue
                vol_pct = int(vol_match.group(1)) / 100.0
                
                # Check if this is a channel main loopback
                for channel, channel_mod in self._channel_loopback_mods.items():
                    if channel_mod == mod_idx:
                        saved = self._config.get("groups", {}).get(channel, {}).get("output_volume", 1.0)
                        if abs(vol_pct - saved) > 0.02:  # 2% threshold
                            self._config.setdefault("groups", {}).setdefault(channel, {})["output_volume"] = vol_pct
                            self.faderSynced.emit(channel, vol_pct)
                        break
        except Exception:
            pass

    # ─── Device/App Refresh ───

    def _refresh_devices(self):
        sinks = pw.list_sinks()
        sources = pw.list_sources()
        self.devicesChanged.emit()

    # ─── Device Polling & SteamVR Resilience ───

    def _check_devices(self):
        """Polling tick: detect device changes, verify loopbacks, track vrserver."""
        try:
            self._check_vrserver_lifecycle()
            msg = self._verify_gaming_output()
            if msg:
                self.statusMessage.emit(msg)
            self._refresh_devices()
        except Exception as e:
            print(f"  Device check error: {e}")

    def _verify_gaming_output(self) -> Optional[str]:
        """Verify the gaming -> hardware loopback target still exists.
        If the hardware sink disappeared (e.g. USB device unplugged, SteamVR cleaned up),
        find a new one and re-establish the link.

        Returns a status message string if action was taken, or None if all good.
        Caller is responsible for emitting any signals on the correct thread.
        """
        hw_output = self._config.get("devices", {}).get("output", "")
        if not hw_output:
            return None

        sinks = pw.list_sinks()
        sink_names = {s.name for s in sinks}

        if hw_output in sink_names:
            # Target exists — check if the loopback itself still exists
            gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
            gaming_monitor = pw.get_sink_monitor_source(gaming_internal) or f"{gaming_internal}.monitor"
            loopback_exists = any(
                src == gaming_monitor and sink == hw_output
                for _, src, sink in pw.list_loopbacks()
            )
            if not loopback_exists:
                print(f"  Device check: gaming -> {hw_output} loopback missing, recreating")
                self._create_gaming_to_hw_loopback(hw_output)
            return None

        # Hardware sink is gone — find a replacement
        new_hw = None
        for s in sinks:
            if not s.name.startswith("pulseforge"):
                new_hw = s.name
                break

        if new_hw:
            print(f"  Device check: output '{hw_output}' gone, switching to '{new_hw}'")
            sink_index = pw.get_sink_index_by_name(new_hw)
            if sink_index is not None:
                pw.set_default_sink(sink_index)
            self._create_gaming_to_hw_loopback(new_hw)
            self._config["devices"]["output"] = new_hw
            config.save_config(self._config)
            friendly = pw._friendly_source_name(new_hw)
            return f"Output restored -> {friendly}"
        else:
            print(f"  Device check: output '{hw_output}' gone, no replacement found")
            return None

    def _check_vrserver_lifecycle(self):
        """Track vrserver process: snapshot routing on start, restore on exit."""
        try:
            r = subprocess.run(["pgrep", "-f", "vrserver"],
                              capture_output=True, text=True, timeout=2)
            vrserver_running = bool(r.stdout.strip())
        except Exception:
            vrserver_running = False

        if vrserver_running and not self._vrserver_was_running and not self._vrserver_recovering:
            # vrserver just started — snapshot current audio state
            print("  SteamVR: vrserver detected, snapshotting audio state")
            self._vrserver_audio_snapshot = {
                "output": self._config.get("devices", {}).get("output", ""),
                "input": self._config.get("devices", {}).get("input", ""),
                "loopbacks": pw.list_loopbacks(),
            }
            self._vrserver_was_running = True

        elif not vrserver_running and self._vrserver_was_running:
            # vrserver just exited — restore audio graph
            print("  SteamVR: vrserver exited, restoring audio routing")
            self._vrserver_was_running = False
            # Defer recovery to a background thread to avoid blocking the Qt event loop
            import threading
            self._vrserver_recovering = True
            def _recover():
                try:
                    self._on_vrserver_exit()
                finally:
                    self._vrserver_recovering = False
            threading.Thread(target=_recover, daemon=True).start()

    def _on_vrserver_exit(self):
        """Restore audio graph after vrserver exits.

        Runs in a background thread to avoid blocking the Qt event loop.
        vrserver leaves phantom PipeWire nodes and may have broken our loopbacks.
        We rebuild the gaming -> hardware path and re-route apps.
        """
        import time

        # Give PipeWire a moment to clean up vrserver's nodes
        time.sleep(1.0)

        # Restore pre-VR output AND input devices from snapshot
        snapshot = self._vrserver_audio_snapshot or {}
        pre_vr_output = snapshot.get("output", "")
        pre_vr_input = snapshot.get("input", "")

        sinks = pw.list_sinks()
        sink_names = {s.name for s in sinks}

        # --- Restore output device ---
        if pre_vr_output and pre_vr_output in sink_names:
            # Full reroute: remove stale loopbacks, set default, create fresh loopback
            gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
            gaming_monitor = pw.get_sink_monitor_source(gaming_internal) or f"{gaming_internal}.monitor"
            # Remove any loopbacks from gaming -> wrong targets (phantom VR sinks, etc.)
            for idx, src, sink in pw.list_loopbacks():
                if src == gaming_monitor and sink != pre_vr_output:
                    pw.remove_loopback(idx)
                    print(f"  SteamVR recovery: removed stale gaming -> {sink} loopback")
            # Set the hardware device as default
            sink_index = pw.get_sink_index_by_name(pre_vr_output)
            if sink_index is not None:
                pw.set_default_sink(sink_index)
            # Restore config and recreate the correct loopback
            self._config["devices"]["output"] = pre_vr_output
            config.save_config(self._config)
            self._create_gaming_to_hw_loopback(pre_vr_output)
            print(f"  SteamVR recovery: output restored -> {pre_vr_output}")

        # --- Restore input device ---
        if pre_vr_input:
            sources = pw.list_sources()
            pre_vr_source = next((s for s in sources if s.name == pre_vr_input), None)
            if pre_vr_source is not None:
                pw.set_default_source(pre_vr_source.id)
                self._mic_chain.set_input_device(pre_vr_input)
                self._config["devices"]["input"] = pre_vr_input
                config.save_config(self._config)
                print(f"  SteamVR recovery: input restored -> {pre_vr_input}")
            else:
                # Saved input device no longer exists — fall back to auto-detect
                print(f"  SteamVR recovery: input '{pre_vr_input}' no longer exists, auto-detecting")
                self._mic_chain.redirect_capture()
        else:
            self._mic_chain.redirect_capture()

        # Re-create any missing channel loopbacks (game/chat/media/aux -> gaming)
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        existing_loopbacks = pw.list_loopbacks()
        for group in ["game", "chat", "media", "aux"]:
            group_internal = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
            monitor = pw.get_sink_monitor_source(group_internal)
            if not monitor:
                continue
            exists = any(src == monitor and sink == gaming_internal for _, src, sink in existing_loopbacks)
            if not exists:
                saved_vol = self._config.get("groups", {}).get(group, {}).get("output_volume", 1.0)
                mod_idx = pw.create_loopback(monitor, gaming_internal,
                                              initial_volume=0.0, ramp_to=saved_vol, ramp_ms=150)
                if mod_idx is not None:
                    self._channel_loopback_mods[group] = mod_idx
                    print(f"  SteamVR recovery: restored {group} -> gaming loopback")

        # Ensure default sink is our gaming sink (not a VR phantom)
        gaming_id = self._group_sink_ids.get("gaming")
        if gaming_id:
            pw.set_default_sink(gaming_id)

        # Re-route any apps that drifted to hardware/phantom sinks back to their groups
        # (subprocess-only, safe from thread)
        from .backend import app_router
        try:
            app_router.route_new_apps_only()
        except Exception:
            pass

        # Restore mic capture if it was disrupted (subprocess-only, safe from thread)
        self._mic_chain.redirect_capture()

        self._vrserver_audio_snapshot = None

        # Emit UI signals on the main thread (Qt requires this)
        from PySide6.QtCore import QTimer
        final_msg = "SteamVR exited — audio routing restored"
        QTimer.singleShot(0, lambda: (
            self.devicesChanged.emit(),
            self.statusMessage.emit(final_msg),
        ))

    @Slot()
    def resyncAudioGraph(self):
        """Manually trigger a full audio graph re-sync.

        Exposed to QML as an 'oh shit' button -- rebuilds loopbacks,
        re-routes apps, and restores mic capture. Useful if something
        goes wrong outside of SteamVR (e.g. WirePlumber hiccup).
        """
        print("  Manual audio graph re-sync triggered")
        msg = self._verify_gaming_output()

        # Re-create missing channel loopbacks
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        existing_loopbacks = pw.list_loopbacks()
        for group in ["game", "chat", "media", "aux"]:
            group_internal = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
            monitor = pw.get_sink_monitor_source(group_internal)
            if not monitor:
                continue
            exists = any(src == monitor and sink == gaming_internal for _, src, sink in existing_loopbacks)
            if not exists:
                saved_vol = self._config.get("groups", {}).get(group, {}).get("output_volume", 1.0)
                mod_idx = pw.create_loopback(monitor, gaming_internal,
                                              initial_volume=0.0, ramp_to=saved_vol, ramp_ms=150)
                if mod_idx is not None:
                    self._channel_loopback_mods[group] = mod_idx
                    print(f"  Re-sync: restored {group} → gaming loopback")

        self._refresh_routing()
        self._mic_chain.redirect_capture()
        self._refresh_devices()
        self.statusMessage.emit(msg or "Audio graph re-synced")

    def _refresh_apps(self):
        try:
            apps = app_router.list_all_apps()
            self.appsChanged.emit()
        except Exception as e:
            print(f"Error refreshing apps: {e}")

    def _refresh_routing(self):
        try:
            # Only route apps that don't already have a valid sink assignment
            # (don't fight manual moves or module-stream-restore)
            app_router.route_new_apps_only()
        except Exception:
            pass

    # ─── QML Slots (called from QML) ───

    @Slot(result='QVariant')
    def getOutputDevices(self):
        sinks = pw.list_sinks()
        # Filter: show hardware + non-pulseforge sinks as output options
        # Includes SteamVR HMD audio devices so users can route to the headset
        result = []
        saved = self._config.get("devices", {}).get("output", "")
        for s in sinks:
            if s.name.startswith("pulseforge"):
                continue
            result.append({"name": s.description, "id": s.id, "internal": s.name, "is_default": s.name == saved})
        return result

    @Slot(result='QVariant')
    def getInputDevices(self):
        sources = pw.list_sources()
        # Includes SteamVR HMD mic so users can route VR mic input through PulseForge
        result = []
        saved = self._config.get("devices", {}).get("input", "")
        for s in sources:
            if s.name.startswith("pulseforge"):
                continue
            result.append({"name": s.description, "id": s.id, "internal": s.name, "is_default": s.name == saved})
        return result

    @Slot(result='QVariant')
    def getStreamDevices(self):
        """Return available input sources for streaming software to capture.
        
        The primary option is pulseforge_stream.monitor (the stream mix output).
        Also lists other virtual input sources that could be used.
        """
        result = []
        saved = self._config.get("devices", {}).get("stream", "pulseforge_stream.monitor")
        sources = pw.list_sources()
        
        # Show pulseforge_stream.monitor first (primary stream input)
        for s in sources:
            if s.name == "pulseforge_stream.monitor":
                result.append({"name": "PulseForge Stream (Virtual Input)", "id": s.id, "internal": s.name, "is_default": True})
                break
        
        # Also show other virtual sources
        for s in sources:
            if s.name.startswith("pulseforge") and s.name != "pulseforge_stream.monitor":
                if ".monitor" in s.name:
                    result.append({"name": s.description, "id": s.id, "internal": s.name, "is_default": s.name == saved})
            elif not s.name.startswith("alsa_input") and ".monitor" not in s.name:
                result.append({"name": s.description, "id": s.id, "internal": s.name, "is_default": s.name == saved})
        
        return result

    @Slot(result='QVariant')
    def getApps(self):
        """Return list of {name, group, id, key, icon} for QML."""
        apps = app_router.list_all_apps()
        result = []
        for app, group in apps:
            app_key = app.name or app.description
            result.append({
                "name": app.description or app.name,
                "group": group,
                "id": app.id,  # pactl sink-input index
                "key": app_key,  # binary name for routing persistence
                "icon": app.icon_name or app.name or ""
            })
        return result

    @Slot(result='QVariant')
    def getChannelGroups(self):
        return ["game", "chat", "media", "aux"]

    @Slot(str)
    def logMessage(self, msg: str):
        print(f"  QML: {msg}", flush=True)

    @Slot(str, result="QVariant")
    def getChannelState(self, channel: str):
        """Return saved state for a channel: volume, mute, stream_enabled, stream_volume."""
        g = self._config.get("groups", {}).get(channel, {})
        return {
            "volume": g.get("output_volume", 1.0),
            "mute": g.get("mute", False),
            "stream_enabled": g.get("stream_enabled", False),
            "stream_volume": g.get("stream_volume", 1.0),
        }

    @Slot(result="QVariant")
    def getMicState(self):
        """Return saved state for mic: volume, mute, stream_enabled, monitor."""
        m = self._config.get("mic", {})
        return {
            "volume": m.get("volume", 1.0),
            "mute": m.get("muted", False),
            "stream_enabled": m.get("stream_enabled", False),
            "monitor": m.get("monitor", False),
        }

    @Slot(str, float)
    def setChannelVolume(self, channel: str, volume: float):
        volume = max(0.0, min(volume, 1.0))  # clamp 0-100%
        # Control volume via the loopback sink-input (channel → gaming),
        # NOT the channel sink itself. This keeps the channel's .monitor
        # at 100% so stream loopbacks are independent of main mix fader.
        mod_idx = self._channel_loopback_mods.get(channel)
        if mod_idx is not None:
            self._set_loopback_volume(mod_idx, volume)
        self._config.setdefault("groups", {}).setdefault(channel, {})["output_volume"] = volume
        config.save_config(self._config)

    @Slot(str, float)
    def setChannelStreamVolume(self, channel: str, volume: float):
        """Set per-channel stream mix volume (controls the loopback sink-input volume)."""
        volume = max(0.0, min(volume, 1.0))  # clamp 0-100%
        self._config.setdefault("groups", {}).setdefault(channel, {})["stream_volume"] = volume
        config.save_config(self._config)

        # Find the loopback module for this channel → stream
        stream_sink = pw._VIRTUAL_SINK_INTERNAL.get("stream", "pulseforge_stream")
        channel_internal = pw._VIRTUAL_SINK_INTERNAL.get(channel, channel)
        channel_monitor = pw.get_sink_monitor_source(channel_internal) or f"{channel_internal}.monitor"

        for mod_idx, src, sink in pw.list_loopbacks():
            if src == channel_monitor and sink == stream_sink:
                self._set_stream_loopback_volume(channel, mod_idx, volume)
                break

    @Slot(str, bool)
    def setChannelStream(self, channel: str, enabled: bool):
        """Toggle streaming for a channel — routes channel audio to pulseforge_stream."""
        self._config.setdefault("groups", {}).setdefault(channel, {})["stream_enabled"] = enabled
        config.save_config(self._config)

        stream_sink = pw._VIRTUAL_SINK_INTERNAL.get("stream", "pulseforge_stream")
        channel_internal = pw._VIRTUAL_SINK_INTERNAL.get(channel, channel)
        channel_monitor = pw.get_sink_monitor_source(channel_internal) or f"{channel_internal}.monitor"

        if enabled:
            # Create loopback: channel.monitor → pulseforge_stream (with volume ramp)
            stream_vol = self._config.get("groups", {}).get(channel, {}).get("stream_volume", 1.0)
            mod_idx = pw.create_loopback(channel_monitor, stream_sink,
                                          initial_volume=0.0, ramp_to=stream_vol, ramp_ms=150)
            if mod_idx is not None:
                print(f"  Stream: {channel} → {stream_sink} (module {mod_idx}, ramped to {stream_vol:.2f})")
        else:
            # Ramp down then remove loopback(s) for this channel → stream
            for idx, src, sink in pw.list_loopbacks():
                if src == channel_monitor and sink == stream_sink:
                    pw.remove_loopback_ramped(idx, ramp_ms=100)
                    print(f"  Stream: removed {channel} → {stream_sink} (ramped)")

    # (moveApp removed — use moveAppToGroup instead)

    # ─── Mic Chain Slots (real-time!) ───

    @Slot(float)
    def setMicVolume(self, volume: float):
        volume = max(0.0, min(volume, 1.0))  # clamp 0-100%
        if self._mic_processed_node:
            pw.set_volume(self._mic_processed_node, volume)
        self._config["mic"]["volume"] = volume
        config.save_config(self._config)

    @Slot(bool)
    def setMicMute(self, muted: bool):
        if self._mic_processed_node:
            pw.set_mute(self._mic_processed_node, muted)
        self._config["mic"]["muted"] = muted
        config.save_config(self._config)

    @Slot(bool)
    def setMicMonitor(self, enabled: bool):
        self._config["mic"]["monitor"] = enabled
        config.save_config(self._config)
        if enabled:
            gaming_sink = "pulseforge_gaming"
            mic_source = "pulseforge.mic.processed"
            existing = pw.list_loopbacks()
            already = any(src == mic_source and sink == gaming_sink for _, src, sink in existing)
            if not already:
                pw.create_loopback(mic_source, gaming_sink,
                                    initial_volume=0.0, ramp_to=1.0, ramp_ms=150)
        else:
            mic_source = "pulseforge.mic.processed"
            for idx, src, sink in pw.list_loopbacks():
                if src == mic_source:
                    pw.remove_loopback_ramped(idx, ramp_ms=100)

    @Slot(bool)
    def setMicStream(self, enabled: bool):
        """Route processed mic to the stream mix (for OBS/streaming)."""
        self._config["mic"]["stream_enabled"] = enabled
        config.save_config(self._config)
        mic_source = "pulseforge.mic.processed"
        stream_sink = "pulseforge_stream"
        if enabled:
            existing = pw.list_loopbacks()
            already = any(src == mic_source and sink == stream_sink for _, src, sink in existing)
            if not already:
                pw.create_loopback(mic_source, stream_sink,
                                    initial_volume=0.0, ramp_to=1.0, ramp_ms=150)
                print(f"  Mic stream: {mic_source} → {stream_sink}")
        else:
            for idx, src, sink in pw.list_loopbacks():
                if src == mic_source and sink == stream_sink:
                    pw.remove_loopback_ramped(idx, ramp_ms=100)
                    print(f"  Mic stream: removed {mic_source} → {stream_sink}")

    @Slot(float)
    def setGateThreshold(self, threshold_db: float):
        self._mic_chain.set_gate(threshold_db=threshold_db)
        self._config["mic"]["gate"]["threshold"] = threshold_db
        config.save_config(self._config)

    @Slot(bool)
    def setGateEnabled(self, enabled: bool):
        self._mic_chain.set_gate(enabled=enabled)
        self._config["mic"]["gate"]["enabled"] = enabled
        config.save_config(self._config)

    @Slot(float)
    def setGateRange(self, range_db: float):
        self._mic_chain.set_gate(range_db=range_db)
        self._config["mic"]["gate"]["range"] = range_db
        config.save_config(self._config)

    @Slot(float)
    def setGateAttack(self, attack_ms: float):
        self._mic_chain.set_gate(attack_ms=attack_ms)
        self._config["mic"]["gate"]["attack"] = attack_ms
        config.save_config(self._config)

    @Slot(float)
    def setGateHold(self, hold_ms: float):
        self._mic_chain.set_gate(hold_ms=hold_ms)
        self._config["mic"]["gate"]["hold"] = hold_ms
        config.save_config(self._config)

    @Slot(float)
    def setGateRelease(self, release_ms: float):
        self._mic_chain.set_gate(release_ms=release_ms)
        self._config["mic"]["gate"]["release"] = release_ms
        config.save_config(self._config)

    # ─── AFX (NVIDIA Audio Effects) Slots ───

    @Slot(result='QVariant')
    def getAfxStatus(self):
        """Return AFX availability and current settings for QML."""
        afx = self._mic_chain._afx
        afx_cfg = self._config.get("mic", {}).get("afx", {})
        return {
            "available": afx.available,
            "enabled": afx_cfg.get("enabled", True),
            "effect_mode": afx_cfg.get("effect_mode", "denoiser"),
            "intensity": int(afx_cfg.get("intensity", 0.7) * 100),
        }

    @Slot(str)
    def setAfxEffectMode(self, mode: str):
        """Change AFX effect mode — requires re-initialization."""
        afx = self._mic_chain._afx
        afx.set_effect_mode(mode)
        self._config.setdefault("mic", {}).setdefault("afx", {})["effect_mode"] = mode
        config.save_config(self._config)
        self.statusMessage.emit(f"AFX mode: {mode}")

    @Slot(float)
    def setAfxIntensity(self, intensity: float):
        """Set AFX intensity (0.0-1.0 from QML slider)."""
        afx = self._mic_chain._afx
        afx.set_intensity(max(0.0, min(1.0, intensity)))
        self._config.setdefault("mic", {}).setdefault("afx", {})["intensity"] = intensity
        config.save_config(self._config)

    @Slot(bool)
    def setAfxEnabled(self, enabled: bool):
        """Toggle AFX on/off."""
        self._mic_chain._afx.set_enabled(enabled)
        self._config.setdefault("mic", {}).setdefault("afx", {})["enabled"] = enabled
        config.save_config(self._config)
        self.statusMessage.emit(f"AFX {'enabled' if enabled else 'disabled'}")

    @Slot(float)
    def setCompThreshold(self, threshold_db: float):
        self._mic_chain.set_compressor(threshold_db=threshold_db)
        self._config["mic"]["compressor"]["threshold"] = threshold_db
        config.save_config(self._config)

    @Slot(float)
    def setCompRatio(self, ratio: float):
        self._mic_chain.set_compressor(ratio=ratio)
        self._config["mic"]["compressor"]["amount"] = ratio
        config.save_config(self._config)

    @Slot(bool)
    def setCompEnabled(self, enabled: bool):
        self._mic_chain.set_compressor(enabled=enabled)
        self._config["mic"]["compressor"]["enabled"] = enabled
        config.save_config(self._config)

    @Slot(float)
    def setCompMakeup(self, makeup_db: float):
        self._mic_chain.set_compressor(makeup_db=makeup_db)
        self._config["mic"]["compressor"]["makeup"] = makeup_db
        config.save_config(self._config)

    @Slot(int, float, float, float)
    def setEqBand(self, idx: int, freq: float, gain_db: float, q: float):
        """Real-time EQ band update — no restart!"""
        self._mic_chain.set_eq_band(idx, freq, gain_db, q)
        bands = self._config["mic"]["eq"].setdefault("bands", [])
        while len(bands) <= idx:
            bands.append({"freq": 1000, "gain": 0, "q": 1.0})
        bands[idx] = {"freq": freq, "gain": gain_db, "q": q}
        config.save_config(self._config)

    @Slot(bool)
    def setEqEnabled(self, enabled: bool):
        self._mic_chain.set_eq_enabled(enabled)
        self._config["mic"]["eq"]["enabled"] = enabled
        config.save_config(self._config)

    @Slot(str)
    def saveEqPreset(self, name: str):
        bands = self._config["mic"]["eq"].get("bands", [])
        config.save_eq_preset(name, bands)

    @Slot(str)
    def loadEqPreset(self, name: str):
        preset = config.load_eq_preset(name)
        if preset and "bands" in preset:
            for i, band in enumerate(preset["bands"][:8]):
                self._mic_chain.set_eq_band(i, band["freq"], band["gain"], band["q"])
            self._config["mic"]["eq"]["bands"] = preset["bands"]
            config.save_config(self._config)

    @Slot(result='QVariant')
    def getEqPresets(self):
        return config.list_eq_presets()

    @Slot(result=str)
    def getCurrentPreset(self):
        """Find which preset matches the current EQ bands, or '' if none."""
        current_bands = self._config.get("mic", {}).get("eq", {}).get("bands", [])
        if not current_bands:
            return ""
        for name in config.list_eq_presets():
            preset = config.load_eq_preset(name)
            if preset and "bands" in preset:
                pb = preset["bands"]
                if len(pb) != len(current_bands):
                    continue
                match = True
                for i in range(len(pb)):
                    if abs(pb[i].get("freq", 0) - current_bands[i].get("freq", 0)) > 1:
                        match = False
                        break
                    if abs(pb[i].get("gain", 0) - current_bands[i].get("gain", 0)) > 0.1:
                        match = False
                        break
                    if abs(pb[i].get("q", 0) - current_bands[i].get("q", 0)) > 0.1:
                        match = False
                        break
                if match:
                    return name
        return ""

    @Slot(result='QVariant')
    def getMicSettings(self):
        """Return all mic settings for QML initialization."""
        mic = self._config.get("mic", {})
        result = {
            "gate": {
                "enabled": mic.get("gate", {}).get("enabled", True),
                "threshold": mic.get("gate", {}).get("threshold", -35.0),
                "attack": mic.get("gate", {}).get("attack", 25.0),
                "hold": mic.get("gate", {}).get("hold", 300.0),
                "release": mic.get("gate", {}).get("release", 200.0),
                "range": mic.get("gate", {}).get("range", -25.0),
            },
            "afx": {
                "available": self._mic_chain._afx.available,
                "enabled": mic.get("afx", {}).get("enabled", True),
                "effect_mode": mic.get("afx", {}).get("effect_mode", "denoiser"),
                "intensity": int(mic.get("afx", {}).get("intensity", 0.7) * 100),
            },
            "compressor": {
                "enabled": mic.get("compressor", {}).get("enabled", True),
                "threshold": mic.get("compressor", {}).get("threshold", -20.0),
                "ratio": mic.get("compressor", {}).get("amount", 3.0),
                "makeup": mic.get("compressor", {}).get("makeup", 0.0),
            },
            "eq": {
                "enabled": mic.get("eq", {}).get("enabled", True),
            },
            "monitor": mic.get("monitor", False),
            "stream_enabled": mic.get("stream_enabled", False),
        }
        print(f"  getMicSettings: {result}", flush=True)
        return result

    @Slot(result='QVariant')
    def getEqBands(self):
        """Return current EQ band settings for QML."""
        bands = self._config.get("mic", {}).get("eq", {}).get("bands", [])
        defaults = [
            {"freq": 60, "gain": 0, "q": 1.0},
            {"freq": 120, "gain": 0, "q": 1.0},
            {"freq": 250, "gain": 0, "q": 1.0},
            {"freq": 500, "gain": 0, "q": 1.0},
            {"freq": 1000, "gain": 0, "q": 1.0},
            {"freq": 2000, "gain": 0, "q": 1.0},
            {"freq": 4000, "gain": 0, "q": 1.0},
            {"freq": 8000, "gain": 0, "q": 1.0},
        ]
        result = []
        for i in range(8):
            if i < len(bands):
                result.append(bands[i])
            else:
                result.append(defaults[i])
        return result

    @Slot(int, str)
    def setOutputDevice(self, node_id: int, name: str):
        """Change the hardware output device — reroutes the gaming mix to the new sink."""
        # Remove old gaming → hardware loopback
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        gaming_monitor = pw.get_sink_monitor_source(gaming_internal) or f"{gaming_internal}.monitor"
        old_hw = self._config.get("devices", {}).get("output", "")
        if old_hw:
            for idx, src, sink in pw.list_loopbacks():
                if src == gaming_monitor and sink == old_hw:
                    pw.remove_loopback(idx)
                    break

        # Set new default and create new loopback
        pw.set_default_sink(node_id)
        self._create_gaming_to_hw_loopback(name)
        self._config["devices"]["output"] = name
        config.save_config(self._config)

    @Slot(int, str)
    def setInputDevice(self, node_id: int, name: str):
        """Change the mic input device — redirects the native chain's pw-cat capture."""
        pw.set_default_source(node_id)
        # Set the configured device and redirect the native chain's capture process
        self._mic_chain.set_input_device(name)
        self._config["devices"]["input"] = name
        config.save_config(self._config)

    @Slot(int, str)
    def setStreamDevice(self, node_id: int, name: str):
        """Change the stream output device — reroutes the stream mix to the new sink."""
        stream_internal = pw._VIRTUAL_SINK_INTERNAL.get("stream", "pulseforge_stream")
        stream_monitor = pw.get_sink_monitor_source(stream_internal) or f"{stream_internal}.monitor"

        # Remove old stream → target loopback
        old_target = self._config.get("devices", {}).get("stream", "")
        if old_target:
            for idx, src, sink in pw.list_loopbacks():
                if src == stream_monitor:
                    pw.remove_loopback_ramped(idx, ramp_ms=100)

        if name and name != "None" and node_id >= 0:
            # Create new loopback: stream monitor → target sink
            pw.create_loopback(stream_monitor, name,
                                initial_volume=0.0, ramp_to=1.0, ramp_ms=150)

        self._config["devices"]["stream"] = name if name != "None" else None
        config.save_config(self._config)

    @Slot(str, str, int)
    def moveAppToGroup(self, app_name: str, group: str, sink_input_id: int):
        """Move an app's audio stream to a different channel group.
        
        Args:
            app_name: display name (for logging)
            group: target group (game/chat/media/aux)
            sink_input_id: pactl sink-input index
        """
        sink_name = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
        sink_index = pw.get_sink_index_by_name(sink_name)
        if sink_index is not None:
            pw.move_stream_to_sink(sink_input_id, sink_index)
            print(f"  Route: {app_name} → {group} (sink-input #{sink_input_id} → sink #{sink_index})")
        else:
            print(f"  Route: failed to find sink for group '{group}'")
        
        # Persist the routing using binary name as key
        routing = config.load_routing()
        routing[app_name] = group
        config.save_routing(routing)
        
        # Refresh apps list in QML
        self._refresh_apps()

    def _set_loopback_mute(self, mod_idx: int, muted: bool, retries: int = 3):
        """Mute/unmute the sink-input created by a loopback module."""
        import time
        mute_str = "1" if muted else "0"
        for attempt in range(retries):
            try:
                r = subprocess.run(
                    ["pactl", "list", "sink-inputs"],
                    capture_output=True, text=True, timeout=5
                )
                blocks = r.stdout.split("Sink Input #")
                for block in blocks[1:]:
                    idx = block.split("\n")[0].strip()
                    if f'pulse.module.id = "{mod_idx}"' in block:
                        subprocess.run(
                            ["pactl", "set-sink-input-mute", idx, mute_str],
                            capture_output=True, timeout=5
                        )
                        return
                if attempt < retries - 1:
                    time.sleep(0.5)
            except Exception as e:
                print(f"  Loopback: failed to set mute for module {mod_idx}: {e}")
                return

    def _set_loopback_volume(self, mod_idx: int, volume: float, retries: int = 3):
        """Find the sink-input created by a loopback module and set its volume.
        
        Loopback modules set 'pulse.module.id' property on their sink-input.
        """
        import time
        vol = max(0.0, min(1.0, volume))
        for attempt in range(retries):
            try:
                r = subprocess.run(
                    ["pactl", "list", "sink-inputs"],
                    capture_output=True, text=True, timeout=5
                )
                blocks = r.stdout.split("Sink Input #")
                for block in blocks[1:]:
                    idx = block.split("\n")[0].strip()
                    if f'pulse.module.id = "{mod_idx}"' in block:
                        subprocess.run(
                            ["pactl", "set-sink-input-volume", idx, f"{vol:.4f}"],
                            capture_output=True, timeout=5
                        )
                        return
                # Sink-input not found yet — wait and retry
                if attempt < retries - 1:
                    time.sleep(0.5)
            except Exception as e:
                print(f"  Loopback: failed to set volume for module {mod_idx}: {e}")
                return

    def _set_stream_loopback_volume(self, channel: str, mod_idx: int, volume: float, retries: int = 3):
        """Find the sink-input created by a loopback module and set its volume.
        
        Loopback modules set 'pulse.module.id' property on their sink-input.
        We search for it on the pulseforge_stream sink.
        """
        import time
        for attempt in range(retries):
            try:
                r = subprocess.run(
                    ["pactl", "list", "sink-inputs"],
                    capture_output=True, text=True, timeout=5
                )
                blocks = r.stdout.split("Sink Input #")
                for block in blocks[1:]:
                    idx = block.split("\n")[0].strip()
                    if f'pulse.module.id = "{mod_idx}"' in block:
                        vol = max(0.0, min(2.0, volume))
                        subprocess.run(
                            ["pactl", "set-sink-input-volume", idx, f"{vol:.4f}"],
                            capture_output=True, timeout=5
                        )
                        return
                # Sink-input not found yet — wait and retry
                if attempt < retries - 1:
                    time.sleep(0.5)
            except Exception as e:
                print(f"  Stream: failed to set volume for {channel}: {e}")
                return

    @Slot(str, bool)
    def setChannelMute(self, channel: str, muted: bool):
        """Mute is global — mutes both the main mix loopback and stream loopback."""
        self._config.setdefault("groups", {}).setdefault(channel, {})["mute"] = muted
        config.save_config(self._config)
        # Mute the main mix loopback (channel → gaming)
        main_mod = self._channel_loopback_mods.get(channel)
        if main_mod is not None:
            self._set_loopback_mute(main_mod, muted)

        # Also mute the stream loopback for this channel
        stream_sink = pw._VIRTUAL_SINK_INTERNAL.get("stream", "pulseforge_stream")
        channel_internal = pw._VIRTUAL_SINK_INTERNAL.get(channel, channel)
        channel_monitor = pw.get_sink_monitor_source(channel_internal) or f"{channel_internal}.monitor"

        for mod_idx, src, sink in pw.list_loopbacks():
            if src == channel_monitor and sink == stream_sink:
                try:
                    r = subprocess.run(
                        ["pactl", "list", "sink-inputs"],
                        capture_output=True, text=True, timeout=5
                    )
                    blocks = r.stdout.split("Sink Input #")
                    for block in blocks[1:]:
                        si_idx = block.split("\n")[0].strip()
                        if f"pulse.module.id = \"{mod_idx}\"" in block:
                            subprocess.run(
                                ["pactl", "set-sink-input-mute", si_idx, "1" if muted else "0"],
                                capture_output=True, timeout=5
                            )
                            break
                except Exception:
                    pass
                break

    # ─── Soundboard Slots ───

    @Slot(int, result='QVariant')
    def getSoundboardSlots(self, page: int):
        """Return list of 9 slot dicts for a page."""
        result = []
        for i in range(9):
            slot = self._soundboard.get_slot(page, i)
            result.append({
                "file_path": slot.file_path if slot else "",
                "name": slot.name if slot else "",
                "volume": slot.volume if slot else 1.0,
            })
        return result

    @Slot(int, int, str, str)
    def assignSound(self, page: int, index: int, file_path: str, name: str):
        """Bind a sound file to a slot."""
        self._soundboard.assign_sound(page, index, file_path, name)

    @Slot(int, int)
    def clearSoundSlot(self, page: int, index: int):
        """Clear a sound slot."""
        self._soundboard.clear_slot(page, index)

    @Slot(int, int)
    def playSound(self, page: int, index: int):
        """Play the sound in a slot."""
        self._soundboard.play_sound(page, index)

    @Slot(int, int, result=bool)
    def isSoundPlaying(self, page: int, index: int):
        return self._soundboard.is_playing(page, index)

    @Slot(int, int)
    def openSoundFileDialog(self, page: int, index: int):
        """Open a file dialog to select a sound file."""
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            None, "Select Sound File", "", "Audio Files (*.wav *.mp3 *.ogg *.flac *.opus)"
        )
        if file_path:
            from pathlib import Path as _P
            self._soundboard.assign_sound(page, index, file_path, _P(file_path).stem)
            # Emit signal so QML refreshes the grid
            self.soundboardChanged.emit(page)

    @Slot(str, result=str)
    def getSoundboardOutput(self):
        """Return the current soundboard output target sink name."""
        return self._soundboard.get_output_target()

    @Slot(str)
    def setSoundboardOutput(self, target: str):
        """Set the soundboard output target (PipeWire sink name).

        Options: pulseforge_gaming (main mix), pulseforge_game, pulseforge_chat,
                pulseforge_media, pulseforge_aux, pulseforge_stream
        """
        self._soundboard.set_output_target(target)
        self.statusMessage.emit(f"Soundboard output: {target}")

    # ─── Channel Recording Slots ───

    @Slot(str)
    def startChannelRecording(self, channel: str):
        """Start recording a channel into a 15s ring buffer."""
        from .backend import pipewire_ctl as pw
        internal = pw._VIRTUAL_SINK_INTERNAL.get(channel, f"pulseforge_{channel}")
        monitor = f"{internal}.monitor"
        self._soundboard.start_recording(channel, monitor)

    @Slot(str)
    def stopChannelRecording(self, channel: str):
        """Stop recording a channel."""
        self._soundboard.stop_recording(channel)

    @Slot(str, result=bool)
    def isChannelRecording(self, channel: str):
        """Check if a channel is being recorded."""
        return self._soundboard.is_channel_recording(channel)

    @Slot(result='QVariant')
    def getRecordingChannels(self):
        """Return list of currently recording channels."""
        return self._soundboard.get_recording_channels()

    @Slot(str, result='QVariant')
    def getChannelWaveform(self, channel: str):
        """Return waveform peaks for a channel's ring buffer."""
        return self._soundboard.get_waveform(channel, 150)

    @Slot(str)
    def captureClip(self, channel: str):
        """Capture a clip from the ring buffer for editing."""
        clip = self._soundboard.capture_clip(channel)
        if clip:
            self.statusMessage.emit(f"Captured {clip.duration:.1f}s clip from {channel}")

    @Slot(result='QVariant')
    def getClipInfo(self):
        """Return info about the current clip for QML."""
        return self._soundboard.get_clip_info()

    @Slot(result='QVariant')
    def getClipWaveform(self):
        """Return waveform peaks for the current captured clip."""
        return self._soundboard.get_clip_waveform(200)

    @Slot(float, float)
    def setClipTrim(self, trim_start: float, trim_end: float):
        """Set the trim region on the current clip (seconds)."""
        self._soundboard.set_clip_trim(trim_start, trim_end)

    @Slot()
    def playClipPreview(self):
        """Play the current trimmed clip."""
        self._soundboard.play_clip_preview()

    @Slot()
    def stopClipPreview(self):
        """Stop clip preview playback."""
        self._soundboard.stop_clip_preview()

    @Slot(result=bool)
    def isClipPlaying(self):
        return self._soundboard.is_clip_playing()

    @Slot()
    def clearClip(self):
        """Discard the current clip."""
        self._soundboard.clear_clip()

    @Slot(result=str)
    def publishClip(self):
        """Publish the current trimmed clip as MP3 to ~/Music/Soundboard REC/.

        Returns the MP3 path on success, empty string on failure.
        After publishing, the clip is available for slot assignment via
        assignPublishedClip().
        """
        path = self._soundboard.publish_clip()
        if path:
            self.statusMessage.emit(f"Published: {Path(path).name}")
        else:
            self.statusMessage.emit("Publish failed")
        return path or ""

    @Slot(result='QVariant')
    def getLastPublished(self):
        """Return info about the last published clip for slot assignment."""
        return self._soundboard.get_last_published()

    @Slot(int, int, result=bool)
    def assignPublishedClip(self, page: int, index: int):
        """Assign the last published MP3 to a soundboard slot.

        Returns True on success.
        """
        ok = self._soundboard.assign_published_clip(page, index)
        if ok:
            self.soundboardChanged.emit(page)
        return ok

    @Slot()
    def clearPublished(self):
        """Cancel slot assignment mode."""
        self._soundboard.clear_published()

    def _cleanup_soundboard(self):
        self._soundboard.cleanup()


def main():
    # Use QApplication (not QGuiApplication) for system tray support
    app = QApplication(sys.argv)
    app.setApplicationName("pulseforge")
    app.setApplicationDisplayName("PulseForge")
    app.setDesktopFileName("pulseforge")
    app.setOrganizationName("PulseForge")
    app.setQuitOnLastWindowClosed(False)  # Keep running when window is closed to tray

    # Set application icon
    icon_path = Path(__file__).parent / "Icon.png"
    if not icon_path.exists():
        icon_path = Path("/home/snow/.openclaw/workspace/sonar-linux/Icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    bridge = PulseForgeBridge()

    # Register system icon provider for app icons
    class SystemIconProvider(QQuickImageProvider):
        def __init__(self):
            super().__init__(QQmlImageProviderBase.ImageType.Image, QQmlImageProviderBase.Flag.ForceAsynchronousImageLoading)

        def requestImage(self, name, size, requestedSize):
            try:
                icon = QIcon.fromTheme(name)
                if icon.isNull():
                    base = name.split('-')[0] if '-' in name else name
                    for candidate in [name, base, name.replace('-', '')]:
                        icon = QIcon.fromTheme(candidate)
                        if not icon.isNull():
                            break
                if icon.isNull():
                    from PySide6.QtGui import QImage
                    img = QImage(1, 1, QImage.Format_ARGB32)
                    img.fill(0)
                    if size:
                        size.setWidth(1)
                        size.setHeight(1)
                    return img
                w = requestedSize.width() if requestedSize.width() > 0 else 28
                h = requestedSize.height() if requestedSize.height() > 0 else 28
                pix = icon.pixmap(QSize(w, h))
                if size:
                    size.setWidth(pix.width())
                    size.setHeight(pix.height())
                return pix.toImage()
            except Exception:
                from PySide6.QtGui import QImage
                img = QImage(1, 1, QImage.Format_ARGB32)
                img.fill(0)
                if size:
                    size.setWidth(1)
                    size.setHeight(1)
                return img

    engine = QQmlApplicationEngine()
    engine.addImageProvider("icons", SystemIconProvider())
    qml_dir = Path(__file__).parent / "qml"
    engine.addImportPath(str(qml_dir))

    # Expose bridge to QML
    engine.rootContext().setContextProperty("PulseForge", bridge)

    main_qml = qml_dir / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))

    if not engine.rootObjects():
        print("Error: Failed to load QML")
        print(f"QML file: {main_qml}")
        sys.exit(1)

    # Get the main window
    root_objects = engine.rootObjects()
    main_window = root_objects[0] if root_objects else None

    # ─── System tray icon ───
    icon_path = Path(__file__).parent / "Icon.png"
    if not icon_path.exists():
        # Fallback: check workspace
        icon_path = Path("/home/snow/.openclaw/workspace/sonar-linux/Icon.png")
    if not icon_path.exists():
        icon_path = Path("pulseforge")  # theme icon name

    tray_icon = QIcon(str(icon_path)) if icon_path.suffix == ".png" else QIcon.fromTheme("pulseforge")

    tray = QSystemTrayIcon(tray_icon, app)
    tray.setToolTip("PulseForge — Virtual Audio Mixer")

    # Tray menu
    tray_menu = QMenu()
    show_action = tray_menu.addAction("Show Mixer")
    show_action.triggered.connect(lambda: (
        main_window.show() if main_window else None,
        main_window.raise_() if main_window else None,
    ))
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("Quit")

    tray.setContextMenu(tray_menu)

    # Click tray to toggle window
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.Trigger:  # single click
            if main_window:
                if main_window.isVisible():
                    main_window.hide()
                else:
                    main_window.show()
                    main_window.raise_()

    tray.activated.connect(on_tray_activated)

    # Handle window close → minimize to tray instead of quitting
    if main_window:
        def on_window_close(event):
            event.ignore()  # Prevent actual close
            main_window.hide()
            tray.showMessage("PulseForge", "Running in system tray. Click tray icon to show.",
                            QSystemTrayIcon.Information, 3000)

        # QML window close handling — intercept the close
        # For QML ApplicationWindow, we need to handle the close differently
        # We'll use the aboutToQuit signal and visibility tracking

    # Start backend after QML is loaded
    bridge.start()

    print("PulseForge running. Minimize to tray or right-click tray icon to quit.")

    # Ensure cleanup on any exit path
    import signal
    _cleanup_done = [False]

    def do_cleanup():
        if _cleanup_done[0]:
            return
        _cleanup_done[0] = True
        try:
            bridge.stop()
        except Exception:
            pass

    def sig_handler(signum, frame):
        do_cleanup()
        os._exit(0)

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    # Quit action cleanup
    def on_quit():
        do_cleanup()
        tray.hide()
        app.quit()

    quit_action.triggered.connect(on_quit)

    # Show tray icon
    tray.show()

    # Safety net: cleanup on any exit path
    app.aboutToQuit.connect(do_cleanup)

    ret = app.exec()
    do_cleanup()
    os._exit(ret)


if __name__ == "__main__":
    main()
