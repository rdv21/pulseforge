"""Config management for PulseForge."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "pulseforge"
CONFIG_FILE = CONFIG_DIR / "config.json"
ROUTING_FILE = CONFIG_DIR / "app-routing.json"
EQ_PRESETS_DIR = CONFIG_DIR / "eq-presets"

DEFAULT_CONFIG = {
    "devices": {
        "output": None,   # sink node name
        "input": None,    # source node name
        "stream": None,   # stream destination sink node name
    },
    "groups": {
        "chat":   {"output_volume": 1.0, "stream_volume": 1.0, "stream_enabled": True,  "muted": False, "solo": False},
        "game":   {"output_volume": 1.0, "stream_volume": 1.0, "stream_enabled": True,  "muted": False, "solo": False},
        "media":  {"output_volume": 1.0, "stream_volume": 1.0, "stream_enabled": False, "muted": False, "solo": False},
        "aux":    {"output_volume": 1.0, "stream_volume": 1.0, "stream_enabled": False, "muted": False, "solo": False},
    },
    "mic": {
        "volume": 1.0,
        "muted": False,
        "monitor": False,
        "gate":      {"threshold": -50.0, "enabled": True},
        "eq":        {"enabled": True, "bands": []},  # populated from preset
        "noise":     {"intensity": 50, "enabled": True},
        "compressor": {"threshold": -20.0, "amount": 3.0, "enabled": True},
    },
    "stream": {
        "volume": 1.0,
        "muted": False,
        "mono_check": False,
    },
    "sample_rate": 48000,
}

DEFAULT_ROUTING = {
    # app name (as reported by PipeWire) -> group name
    # empty = all new apps go to "aux"
}


def ensure_dirs():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    EQ_PRESETS_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            # merge with defaults for any missing keys
            return _deep_merge(DEFAULT_CONFIG.copy(), cfg)
        except (json.JSONDecodeError, IOError):
            pass
    # First run — auto-detect hardware
    cfg = DEFAULT_CONFIG.copy()
    _auto_detect_devices(cfg)
    save_config(cfg)
    return cfg


def save_config(config):
    ensure_dirs()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_routing():
    ensure_dirs()
    if ROUTING_FILE.exists():
        try:
            with open(ROUTING_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return dict(DEFAULT_ROUTING)


def save_routing(routing):
    ensure_dirs()
    with open(ROUTING_FILE, "w") as f:
        json.dump(routing, f, indent=2)


def list_eq_presets():
    ensure_dirs()
    presets = []
    for p in EQ_PRESETS_DIR.glob("*.json"):
        presets.append(p.stem)
    return sorted(presets)


def load_eq_preset(name):
    path = EQ_PRESETS_DIR / f"{name}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def save_eq_preset(name, bands):
    ensure_dirs()
    path = EQ_PRESETS_DIR / f"{name}.json"
    with open(path, "w") as f:
        json.dump({"bands": bands}, f, indent=2)


def _deep_merge(base, override):
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _auto_detect_devices(cfg):
    """Auto-detect hardware input/output devices on first run.

    Sets sensible defaults without requiring user interaction.
    """
    import subprocess, re

    # Detect default output sink
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True, timeout=5
        )
        default_sink = None
        best_sink = None
        for line in r.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1]
            if "pulseforge" in name:
                continue
            # Prefer hardware sinks (alsa)
            if "alsa_output" in name:
                if best_sink is None:
                    best_sink = name
                # Prefer HDMI or analog stereo as default
                if "hdmi-stereo" in name or "analog-stereo" in name:
                    best_sink = name
                    break
        if best_sink:
            cfg["devices"]["output"] = best_sink
            print(f"  Auto-detect: output → {best_sink}")
    except Exception:
        pass

    # Detect input source (any hardware mic)
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True, text=True, timeout=5
        )
        best_input = None
        for line in r.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1]
            if ".monitor" in name or "pulseforge" in name:
                continue
            if "alsa_input" in name:
                if best_input is None:
                    best_input = name
                # Prefer USB mics (Scarlett, Blue Yeti, etc)
                if "usb-" in name and "Mic" in name:
                    best_input = name
                    break
        if best_input:
            cfg["devices"]["input"] = best_input
            print(f"  Auto-detect: input → {best_input}")
    except Exception:
        pass

    # Stream device defaults to the same output
    cfg.setdefault("devices", {})["stream"] = cfg["devices"].get("output")
