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
from .backend.native_chain import NativeMicChain
from .backend.vu_meter import get_poller
from .backend.process_manager import get_manager


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

    # ─── Lifecycle ───

    def start(self):
        """Called after QML is loaded. Sets up audio graph and starts chains."""
        self._setup_audio_graph()
        self._start_mic_chain()
        self._restore_mic_stream()
        self._setup_vu_meters()
        self._refresh_devices()
        self._refresh_apps()

        self._vu_timer.start()
        self._app_timer.start()
        self._route_timer.start()
        self._fader_sync_timer.start()

    def stop(self):
        """Clean shutdown — stop everything and remove PipeWire objects."""
        self._vu_timer.stop()
        self._app_timer.stop()
        self._route_timer.stop()
        self._fader_sync_timer.stop()
        self._vu_poller.stop()
        self._mic_chain.stop()
        get_manager().stop_all()

        # Kill any remaining pw-cat processes
        try:
            subprocess.run(["pkill", "-9", "-f", "pw-cat.*pulseforge"],
                           capture_output=True, timeout=3)
        except Exception:
            pass

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
        subprocess.run(["pkill", "-9", "-f", "pw-cat.*pulseforge"],
                       capture_output=True, timeout=3)
        time.sleep(0.5)
        # Remove all loopbacks
        pw.remove_all_pulseforge_loopbacks()
        # Remove virtual source modules (stream_input)
        pw.remove_all_pulseforge_virtual_sources()
        # Destroy virtual sources (mic.processed etc)
        pw.destroy_all_pulseforge_sources()
        # Destroy virtual sinks
        pw.destroy_all_pulseforge_sinks()
        time.sleep(1.0)

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

        gaming_id = self._group_sink_ids.get("gaming")
        if gaming_id is None:
            print("  WARNING: Gaming sink not created")
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
                    mod_idx = pw.create_loopback(monitor, gaming_internal)
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
                    mod_idx = pw.create_loopback(group_monitor, stream_sink)
                    if mod_idx is not None:
                        stream_vol = group_cfg.get("stream_volume", 1.0)
                        self._set_stream_loopback_volume(group, mod_idx, stream_vol)
                        print(f"  Stream loopback: {group} → {stream_sink} (vol: {stream_vol:.2f})")

    def _create_gaming_to_hw_loopback(self, hw_sink_name: str):
        gaming_internal = pw._VIRTUAL_SINK_INTERNAL["gaming"]
        gaming_monitor = pw.get_sink_monitor_source(gaming_internal) or f"{gaming_internal}.monitor"
        existing = pw.list_loopbacks()
        for _, src, sink in existing:
            if src == gaming_monitor and sink == hw_sink_name:
                return
        pw.create_loopback(gaming_monitor, hw_sink_name)

    def _restore_mic_stream(self):
        """Restore mic → stream and mic → monitor loopbacks if enabled in config."""
        mic_source = "pulseforge.mic.processed"
        # Restore mic → stream
        if self._config.get("mic", {}).get("stream_enabled", False):
            stream_sink = "pulseforge_stream"
            existing = pw.list_loopbacks()
            already = any(src == mic_source and sink == stream_sink for _, src, sink in existing)
            if not already:
                pw.create_loopback(mic_source, stream_sink)
                print(f"  Mic stream restored: {mic_source} → {stream_sink}")
        # Restore mic → monitor (gaming)
        if self._config.get("mic", {}).get("monitor", False):
            gaming_sink = "pulseforge_gaming"
            existing = pw.list_loopbacks()
            already = any(src == mic_source and sink == gaming_sink for _, src, sink in existing)
            if not already:
                pw.create_loopback(mic_source, gaming_sink)
                print(f"  Mic monitor restored: {mic_source} → {gaming_sink}")

    def _start_mic_chain(self):
        """Start the native Python mic processing chain."""
        cfg = self._config.get("mic", {})

        # Apply saved settings to the chain before starting
        gate_cfg = cfg.get("gate", {})
        self._mic_chain.gate.set_threshold(gate_cfg.get("threshold", -50.0))
        self._mic_chain.gate.set_enabled(gate_cfg.get("enabled", True))

        eq_cfg = cfg.get("eq", {})
        eq_bands = eq_cfg.get("bands", [])
        if eq_bands:
            self._mic_chain.set_eq_bands(eq_bands, eq_cfg.get("enabled", True))

        noise_cfg = cfg.get("noise", {})
        intensity = noise_cfg.get("intensity", 50)
        self._mic_chain.set_noise(intensity=float(intensity), enabled=noise_cfg.get("enabled", True))

        comp_cfg = cfg.get("compressor", {})
        self._mic_chain.set_compressor(
            threshold_db=comp_cfg.get("threshold", -20.0),
            ratio=comp_cfg.get("amount", 3.0),
            makeup_db=comp_cfg.get("makeup", 0.0),
            enabled=comp_cfg.get("enabled", True),
        )

        self._mic_chain.start()

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
        
        # Stream VU: what enters the stream mix (pre-stream-fader = raw monitor level)
        # Shows the signal available to the stream, before stream fader is applied
        if group_cfg.get("stream_enabled", False):
            stream_peak = peak
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
                pw.create_loopback(mic_source, gaming_sink)
        else:
            mic_source = "pulseforge.mic.processed"
            for idx, src, sink in pw.list_loopbacks():
                if src == mic_source:
                    pw.remove_loopback(idx)

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
                pw.create_loopback(mic_source, stream_sink)
                print(f"  Mic stream: {mic_source} → {stream_sink}")
        else:
            for idx, src, sink in pw.list_loopbacks():
                if src == mic_source and sink == stream_sink:
                    pw.remove_loopback(idx)
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
    def setNoiseIntensity(self, intensity: float):
        """Set noise suppression intensity (0.0-1.0 from QML slider)."""
        self._mic_chain.set_noise(intensity=intensity * 100.0)
        self._config["mic"]["noise"]["intensity"] = int(intensity * 100)
        config.save_config(self._config)

    @Slot(bool)
    def setNoiseEnabled(self, enabled: bool):
        self._mic_chain.set_noise(enabled=enabled)
        self._config["mic"]["noise"]["enabled"] = enabled
        config.save_config(self._config)

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
            },
            "noise": {
                "enabled": mic.get("noise", {}).get("enabled", True),
                "intensity": mic.get("noise", {}).get("intensity", 50),
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
                    pw.remove_loopback(idx)

        if name and name != "None" and node_id >= 0:
            # Create new loopback: stream monitor → target sink
            pw.create_loopback(stream_monitor, name)

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
