"""PipeWire/WirePlumber control interface.

Handles device listing, volume control, link management, and routing
by shelling out to wpctl, pw-cli, and pactl.
"""
import subprocess
import json
import re
import time
import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioNode:
    id: int
    name: str
    description: str
    media_class: str
    volume: float = 1.0
    muted: bool = False
    is_default: bool = False
    icon_name: str = ""


@dataclass
class AudioLink:
    id: int
    input_node: int
    output_node: int
    input_port: int
    output_port: int


def _run(cmd: list[str], timeout: int = 5) -> str:
    """Run a command and return stdout."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _run_json(cmd: list[str]) -> dict | list:
    out = _run(cmd)
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def _strip_box(line: str) -> str:
    """Strip box-drawing characters and whitespace from a wpctl status line."""
    return line.strip().lstrip('│├─└ ').strip()


# ─── Device Listing ───────────────────────────────────────────────

def list_sinks() -> list[AudioNode]:
    """List all audio output sinks (playback devices)."""
    return _list_sinks_pactl()


def list_sources() -> list[AudioNode]:
    """List all audio input sources (recording devices)."""
    return _list_sources_pactl()


def _list_sinks_pactl() -> list[AudioNode]:
    """List sinks using pactl — shows ALL sinks including virtual and individual hardware inputs."""
    out = _run(["pactl", "list", "short", "sinks"], timeout=5)
    if not out:
        return []
    
    nodes = []
    default_sink = _get_default_node_name("sink")
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        idx = int(parts[0])
        name = parts[1]
        # Get full info from pactl list sinks
        desc = name
        vol = 1.0
        muted = False
        is_default = (name == default_sink)
        nodes.append(AudioNode(
            id=idx,
            name=name,
            description=_friendly_source_name(name),
            media_class="Audio/Sink",
            volume=vol,
            muted=muted,
            is_default=is_default,
        ))
    return nodes


def _list_sources_pactl() -> list[AudioNode]:
    """List sources using pactl — shows ALL sources including individual hardware inputs.
    
    wpctl status groups multiple hardware inputs (e.g. Scarlett Solo Mic1/Mic2/Line2)
    into a single device entry, hiding individual sources. pactl shows them all.
    """
    out = _run(["pactl", "list", "short", "sources"], timeout=5)
    if not out:
        return []
    
    nodes = []
    default_source = _get_default_node_name("source")
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        idx = int(parts[0])
        name = parts[1]
        # Skip monitor sources (they're not input devices)
        if name.endswith(".monitor"):
            continue
        is_default = (name == default_source)
        nodes.append(AudioNode(
            id=idx,
            name=name,
            description=_friendly_source_name(name),
            media_class="Audio/Source",
            volume=1.0,
            muted=False,
            is_default=is_default,
        ))
    return nodes


def _get_default_node_name(kind: str) -> str:
    """Get the default sink or source name."""
    cmd = ["pactl", "get-default-sink"] if kind == "sink" else ["pactl", "get-default-source"]
    out = _run(cmd, timeout=3)
    return out.strip() if out else ""


def _friendly_source_name(name: str) -> str:
    """Map internal source/sink names to friendly display names."""
    _pf_names = {
        "pulseforge_game": "PulseForge Game",
        "pulseforge_chat": "PulseForge Chat",
        "pulseforge_media": "PulseForge Media",
        "pulseforge_aux": "PulseForge Aux",
        "pulseforge_stream": "PulseForge Stream",
        "pulseforge_gaming": "PulseForge Gaming (Full Mix)",
        "pulseforge.mic.processed": "PulseForge Processed Mic",
        "pulseforge.mic.capture": "PulseForge Mic Capture",
    }
    if name in _pf_names:
        return _pf_names[name]
    # Scarlett Solo
    if "HiFi__Mic2__source" in name:
        return "Scarlett Solo Input 2 (Mic)"
    if "HiFi__Mic1__source" in name:
        return "Scarlett Solo Input 1 (Inst/Line)"
    if "HiFi__Line2__source" in name:
        return "Scarlett Solo Line 2"
    if "HiFi__Line1__sink" in name:
        return "Scarlett Solo Line 1"
    # Insta360
    if "Insta360" in name:
        return "Insta360 Link Mono"
    # SteamVR / Valve Index HMD audio
    if "indexhmd" in name.lower() or "steamvr" in name.lower():
        if "source" in name.lower() or "mic" in name.lower() or "input" in name.lower():
            return "SteamVR HMD Mic"
        return "SteamVR HMD Audio"
    if "vridge" in name.lower():
        return "SteamVR Audio"
    # Generic fallback — prettify the name
    pretty = name.replace("alsa_input.", "").replace("alsa_output.", "")
    pretty = pretty.replace("usb-", "").replace(".analog-stereo", "")
    pretty = pretty.replace(".mono-fallback", "").replace("-", " ")
    return pretty[:60]


def _list_nodes(kind: str) -> list[AudioNode]:
    """Use wpctl to list sinks or sources."""
    out = _run(["wpctl", "status"])
    if not out:
        return []

    nodes = []
    in_sinks = False
    in_sources = False
    in_audio = False

    for line in out.split("\n"):
        stripped = _strip_box(line)
        if not stripped:
            continue

        # Track which section we're in — only within Audio block
        if stripped == "Audio":
            in_audio = True
            continue
        elif stripped in ("Video", "Settings"):
            in_audio = False
            in_sinks = False
            in_sources = False
            continue

        if not in_audio:
            continue

        if "Sinks:" in stripped:
            in_sinks = True
            in_sources = False
            continue
        elif "Sources:" in stripped:
            in_sinks = False
            in_sources = True
            continue
        elif stripped.startswith("Filters:") or stripped.startswith("Streams:"):
            in_sinks = False
            in_sources = False
            continue

        target_section = in_sinks if kind == "sink" else in_sources
        if not target_section:
            continue

        # Parse line like: "*  59. SMSL USB AUDIO Analog Stereo [vol: 1.00]"
        # Also handles lines without [vol: ...] (e.g. V4L2 devices)
        match = re.match(r"^(\*?\s*)(\d+)\.\s+(.+?)(?:\s+\[vol:\s+([\d.]+)\])?(?:\s+\[MUTED\])?$", stripped)
        if match:
            is_default = "*" in match.group(1)
            node_id = int(match.group(2))
            desc = match.group(3).strip()
            vol_str = match.group(4)
            vol = float(vol_str) if vol_str else 1.0
            name = _get_node_name(node_id)
            nodes.append(AudioNode(
                id=node_id,
                name=name,
                description=desc,
                media_class="Audio/Sink" if kind == "sink" else "Audio/Source",
                volume=vol,
                is_default=is_default,
            ))

    return nodes


def _get_node_name(node_id: int) -> str:
    """Get the internal node name for a node ID."""
    out = _run(["wpctl", "inspect", str(node_id)])
    for line in out.split("\n"):
        s = line.strip().lstrip('*').strip()
        if s.startswith("node.name"):
            val = s.split("=", 1)[1].strip().strip('"') if "=" in s else ""
            return val
        if s.startswith("name:"):
            return s.split(":", 1)[1].strip()
    return ""


# ─── Volume & Mute ─────────────────────────────────────────────────

def get_volume(node_id: int) -> float:
    out = _run(["wpctl", "get-volume", str(node_id)])
    match = re.search(r"Volume:\s+([\d.]+)", out)
    if match:
        return float(match.group(1))
    return 1.0


def is_muted(node_id: int) -> bool:
    out = _run(["wpctl", "get-volume", str(node_id)])
    return "MUTED" in out


def set_volume(node_id: int, volume: float):
    vol = max(0.0, min(2.0, volume))
    _run(["wpctl", "set-volume", str(node_id), f"{vol:.4f}"])


def adjust_volume(node_id: int, delta: float):
    _run(["wpctl", "set-volume", str(node_id), f"{delta:+.4f}"])


def set_mute(node_id: int, muted: bool):
    state = "1" if muted else "0"
    _run(["wpctl", "set-mute", str(node_id), state])


def toggle_mute(node_id: int):
    _run(["wpctl", "set-mute", str(node_id), "toggle"])


# ─── Default Device ────────────────────────────────────────────────

def set_default_sink(node_id: int):
    _run(["wpctl", "set-default", str(node_id)])


def set_default_source(node_id: int):
    _run(["wpctl", "set-default", str(node_id)])


# ─── Virtual Sink Management ───────────────────────────────────────

VIRTUAL_SINK_NAMES = {
    "chat":   "PulseForge Chat",
    "game":   "PulseForge Game",
    "media":  "PulseForge Media",
    "aux":    "PulseForge Aux",
    "stream": "PulseForge Stream",
    "gaming": "PulseForge Gaming",
}

# Internal names used as PipeWire node.name
_VIRTUAL_SINK_INTERNAL = {
    "chat":   "pulseforge_chat",
    "game":   "pulseforge_game",
    "media":  "pulseforge_media",
    "aux":    "pulseforge_aux",
    "stream": "pulseforge_stream",
    "gaming": "pulseforge_gaming",
}

# Default group for new/unassigned apps
DEFAULT_GROUP = "aux"


def create_virtual_sink(group: str, display_name: str = "") -> Optional[int]:
    """Create a virtual sink using pactl module-null-sink.

    Returns the node ID, or None on failure.
    """
    display = display_name or VIRTUAL_SINK_NAMES.get(group, f"PulseForge {group.capitalize()}")
    internal = _VIRTUAL_SINK_INTERNAL.get(group, group)

    # Check if already exists (using pactl, not pw-cli — module-null-sink
    # sinks live in the PulseAudio compat layer and aren't visible to pw-cli)
    existing = get_node_by_name(internal)
    if existing is not None:
        return existing

    display_no_spaces = display.replace(" ", "_")
    cmd = [
        "pactl", "load-module", "module-null-sink",
        f"sink_name={internal}",
        f"sink_properties=device.description={display_no_spaces}",
    ]
    out = _run(cmd, timeout=5)
    if out and out.strip():
        import time
        time.sleep(0.3)
        node_id = get_node_by_name(internal)
        if node_id is None:
            time.sleep(0.3)
            node_id = get_node_by_name(internal)
        return node_id
    return None


def create_all_virtual_sinks() -> dict[str, Optional[int]]:
    """Create all PulseForge virtual sinks. Returns {group: node_id}."""
    results = {}
    for group in VIRTUAL_SINK_NAMES:
        results[group] = create_virtual_sink(group)
    return results


def remove_node(node_id: int):
    _run(["pw-cli", "destroy", str(node_id)])


def get_node_by_name(name: str) -> Optional[int]:
    """Find a node ID by its name property.
    Tries pw-cli first (native PipeWire nodes), falls back to pactl (PulseAudio compat sinks).
    """
    # Try pw-cli first (works for native PipeWire nodes like filter-chain sources)
    out = _run(["pw-cli", "list-objects", "Node"], timeout=10)
    if out:
        current_id = None
        for line in out.split("\n"):
            line = line.strip()
            id_match = re.match(r"id\s+(\d+)", line)
            if id_match:
                current_id = int(id_match.group(1))
                continue
            if current_id is not None and f'name = "{name}"' in line:
                return current_id
            if current_id is not None and f"name = {name}" in line and "=" in line:
                return current_id

    # Fall back to pactl list sinks (module-null-sink created via PA compat)
    # These sinks aren't visible to pw-cli, so check via pactl
    out = _run(["pactl", "list", "short", "sinks"], timeout=5)
    if out:
        for line in out.split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == name:
                # Return the pactl sink index as a stand-in ID.
                # This is NOT the pw-cli node ID, but it's truthy and unique.
                return int(parts[0])

    # Also check sources (for mic.processed etc)
    out = _run(["pactl", "list", "short", "sources"], timeout=5)
    if out:
        for line in out.split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == name:
                return int(parts[0])

    return None


# ─── Link Management (Routing) ─────────────────────────────────────

def list_links() -> list[AudioLink]:
    """List all active port links."""
    out = _run(["pw-cli", "list-objects", "Link"], timeout=10)
    links = []
    current_id = None
    current_input = None
    current_output = None

    for line in out.split("\n"):
        line = line.strip()
        id_match = re.match(r"id\s+(\d+)", line)
        if id_match:
            if current_id is not None and current_input is not None and current_output is not None:
                links.append(AudioLink(
                    id=current_id,
                    input_node=current_input,
                    output_node=current_output,
                    input_port=0,
                    output_port=0,
                ))
            current_id = int(id_match.group(1))
            current_input = None
            current_output = None
            continue
        if "link.input.node" in line:
            match = re.search(r"= (\d+)", line)
            if match:
                current_input = int(match.group(1))
        elif "link.output.node" in line:
            match = re.search(r"= (\d+)", line)
            if match:
                current_output = int(match.group(1))

    if current_id is not None and current_input is not None and current_output is not None:
        links.append(AudioLink(
            id=current_id,
            input_node=current_input,
            output_node=current_output,
            input_port=0,
            output_port=0,
        ))

    return links


def create_link(output_node: int, input_node: int) -> Optional[int]:
    """Link output of one node to input of another."""
    out = _run(["pw-cli", "create-link", str(output_node), str(input_node)])
    if out:
        try:
            return int(out.strip())
        except ValueError:
            pass
    return None


def destroy_link(link_id: int):
    _run(["pw-cli", "destroy", str(link_id)])


def unlink_nodes(output_node: int, input_node: int):
    """Remove all links between two nodes."""
    links = list_links()
    for link in links:
        if link.output_node == output_node and link.input_node == input_node:
            destroy_link(link.id)


# ─── Stream (App) Management ───────────────────────────────────────

def list_streams() -> list[AudioNode]:
    """List all active audio streams (apps producing audio)."""
    out = _run(["wpctl", "status"])
    if not out:
        return []

    streams = []
    in_streams = False
    in_audio = False

    for line in out.split("\n"):
        stripped = _strip_box(line)
        if not stripped:
            continue

        if stripped == "Audio":
            in_audio = True
            in_streams = False
            continue
        elif stripped in ("Video", "Settings"):
            in_audio = False
            in_streams = False
            continue

        if not in_audio:
            continue

        if "Streams:" in stripped:
            in_streams = True
            continue
        if in_streams:
            # End of streams section
            if stripped.startswith("Filters:") or stripped == "":
                in_streams = False
                continue
            # Parse stream lines like: "*  151. Steam   [vol: 1.00]"
            match = re.match(r"^(\*?\s*)(\d+)\.\s+(.+?)(?:\s+\[vol:\s+([\d.]+)\])?(?:\s+\[MUTED\])?$", stripped)
            if match:
                node_id = int(match.group(2))
                desc = match.group(3).strip()
                vol_str = match.group(4)
                vol = float(vol_str) if vol_str else 1.0
                name = _get_node_name(node_id)
                muted = "[MUTED]" in stripped
                streams.append(AudioNode(
                    id=node_id,
                    name=name,
                    description=desc,
                    media_class="Stream/Output/Audio",
                    volume=vol,
                    muted=muted,
                ))

    return streams


def list_sink_inputs() -> list[AudioNode]:
    """List active sink inputs (app audio streams) using pactl.

    This is more reliable than wpctl for getting the app's target sink.
    """
    out = _run(["pactl", "list", "sink-inputs"])
    if not out:
        return []

    apps = []
    current = {}

    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("Sink Input #"):
            if current.get("id") is not None:
                apps.append(_sink_input_to_node(current))
            current = {"id": int(line.split("#")[1].strip())}
        elif line.startswith("Sink:"):
            current["sink_id"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("application.name"):
            current["app_name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("media.name"):
            current["media_name"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("Mute:"):
            current["muted"] = line.split(":", 1)[1].strip() == "yes"
        elif line.startswith("Volume:"):
            vol_match = re.search(r"(\d+)%", line)
            if vol_match:
                current["volume"] = int(vol_match.group(1)) / 100.0
        elif line.startswith("application.process.binary"):
            current["binary"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("application.icon_name"):
            current["icon_name"] = line.split("=", 1)[1].strip().strip('"')

    if current.get("id") is not None:
        apps.append(_sink_input_to_node(current))

    # Filter out loopback-generated streams (not real user apps)
    apps = [a for a in apps if not (
        a.description and a.description.startswith("loopback-")
    )]
    # Also filter out streams without a real application binary
    apps = [a for a in apps if not a.name.startswith("app-")]

    return apps


def _sink_input_to_node(info: dict) -> AudioNode:
    """Convert pactl sink-input dict to AudioNode."""
    return AudioNode(
        id=info["id"],
        name=info.get("binary", info.get("app_name", f"app-{info['id']}")),
        description=info.get("app_name", info.get("media_name", info.get("binary", f"App {info['id']}"))),
        media_class="Stream/Output/Audio",
        volume=info.get("volume", 1.0),
        muted=info.get("muted", False),
        icon_name=info.get("icon_name", ""),
    )


def get_stream_sink_id(sink_input_id: int) -> Optional[int]:
    """Get the sink ID that a sink-input is currently routed to."""
    out = _run(["pactl", "list", "sink-inputs"])
    target = f"Sink Input #{sink_input_id}"
    in_target = False

    for line in out.split("\n"):
        line = line.strip()
        if line == target:
            in_target = True
            continue
        if in_target and line.startswith("Sink Input #"):
            break
        if in_target and line.startswith("Sink:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return None


def move_stream_to_sink(sink_input_id: int, sink_index: int):
    """Move an app's audio stream to a different sink.

    Uses pactl move-sink-input. With PipeWire, moving by sink name is more reliable
    than by index. So we look up the name from the index and use that.
    """
    # Get the sink name from the index
    out = _run(["pactl", "list", "short", "sinks"], timeout=5)
    sink_name = None
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == str(sink_index):
            sink_name = parts[1]
            break
    
    if sink_name:
        _run(["pactl", "move-sink-input", str(sink_input_id), sink_name])
    else:
        _run(["pactl", "move-sink-input", str(sink_input_id), str(sink_index)])


def get_stream_target_for_app(app_name: str) -> Optional[int]:
    """Get the sink node ID that an app is currently routed to."""
    apps = list_sink_inputs()
    for app in apps:
        if app.name == app_name or app.description == app_name:
            return get_stream_sink_id(app.id)
    return None


def get_sink_index_by_name(sink_name: str) -> Optional[int]:
    """Get the pactl sink index for a sink by its Name property.
    
    This is the index used by `pactl move-sink-input`, NOT the PipeWire node ID.
    Returns the first column from `pactl list short sinks`.
    """
    out = _run(["pactl", "list", "short", "sinks"], timeout=5)
    if not out:
        return None
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1] == sink_name:
            return int(parts[0])
    return None


# ─── Loopback Management (Audio Routing) ────────────────────────

def create_loopback(source_name: str, sink_name: str, initial_volume: float = 0.0,
                      ramp_to: float = None, ramp_ms: int = 150) -> Optional[int]:
    """Load a module-loopback connecting a source to a sink.
    
    Args:
        initial_volume: Starting volume (0.0-1.0) to prevent popping.
        ramp_to: If set, ramp volume from initial_volume to this over ramp_ms.
    Returns the module index, or None on failure.
    """
    cmd = [
        "pactl", "load-module", "module-loopback",
        f"source={source_name}",
        f"sink={sink_name}",
        "latency_msec=20",
    ]
    out = _run(cmd)
    if out:
        try:
            mod_idx = int(out.strip())
            # Set initial volume to prevent popping
            if initial_volume < 1.0:
                _set_loopback_initial_volume(mod_idx, initial_volume)
            # Ramp to target if requested
            if ramp_to is not None and ramp_to > initial_volume:
                _ramp_loopback_volume(mod_idx, initial_volume, ramp_to, ramp_ms)
            return mod_idx
        except ValueError:
            pass
    return None


def _set_loopback_initial_volume(mod_idx: int, volume: float):
    """Set initial volume on a freshly created loopback to prevent pop/click."""
    try:
        r = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True, text=True, timeout=5
        )
        blocks = r.stdout.split("Sink Input #")
        for block in blocks[1:]:
            si_idx = block.split("\n")[0].strip()
            if f'pulse.module.id = "{mod_idx}"' in block:
                subprocess.run(
                    ["pactl", "set-sink-input-volume", si_idx, f"{volume:.4f}"],
                    capture_output=True, timeout=5
                )
                return
    except Exception:
        pass


def _ramp_loopback_volume(mod_idx: int, from_vol: float, to_vol: float, duration_ms: int):
    """Ramp a loopback's volume from from_vol to to_vol over duration_ms.
    Runs in a background thread to avoid blocking."""
    def _ramp():
        steps = max(5, duration_ms // 20)  # 20ms per step
        step_vol = (to_vol - from_vol) / steps
        vol = from_vol
        for _ in range(steps):
            vol += step_vol
            vol = max(0.0, min(1.0, vol))
            _set_loopback_initial_volume(mod_idx, vol)
            time.sleep(0.02)
        # Ensure we hit the target exactly
        _set_loopback_initial_volume(mod_idx, to_vol)
    threading.Thread(target=_ramp, daemon=True).start()


def remove_loopback(module_index: int):
    """Unload a loopback module by its index."""
    _run(["pactl", "unload-module", str(module_index)])


def remove_loopback_ramped(module_index: int, ramp_ms: int = 100):
    """Ramp volume to 0 before unloading to prevent pop/click."""
    # Ramp down
    _ramp_loopback_volume(module_index, 1.0, 0.0, ramp_ms)
    # Wait for ramp to complete, then unload
    time.sleep(ramp_ms / 1000.0 + 0.05)
    _run(["pactl", "unload-module", str(module_index)])


def list_loopbacks() -> list[tuple[int, str, str]]:
    """List all loaded loopback modules. Returns [(module_index, source, sink), ...]"""
    out = _run(["pactl", "list", "short", "modules"], timeout=5)
    if not out:
        return []
    
    result = []
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3 and "module-loopback" in parts[1]:
            idx = int(parts[0])
            args = parts[2] if len(parts) > 2 else ""
            source = ""
            sink = ""
            for arg in args.split():
                if arg.startswith("source="):
                    source = arg.split("=", 1)[1]
                elif arg.startswith("sink="):
                    sink = arg.split("=", 1)[1]
            result.append((idx, source, sink))
    return result


def remove_loopbacks_for_sink(sink_name: str):
    """Remove all loopback modules targeting a specific sink."""
    for idx, source, sink in list_loopbacks():
        if sink == sink_name:
            remove_loopback(idx)


def remove_all_pulseforge_loopbacks():
    """Remove ALL loopback modules related to PulseForge."""
    # Collect first, then remove (avoid modifying while iterating)
    to_remove = []
    for idx, source, sink in list_loopbacks():
        if "pulseforge" in source or "pulseforge" in sink:
            to_remove.append(idx)
    for idx in to_remove:
        remove_loopback(idx)
    if to_remove:
        print(f"  Cleanup: removed {len(to_remove)} PulseForge loopbacks")


def remove_all_pulseforge_virtual_sources():
    """Remove all virtual source modules created by PulseForge."""
    out = _run(["pactl", "list", "short", "modules"], timeout=5)
    if not out:
        return
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3 and "module-virtual-source" in parts[1] and "pulseforge" in parts[2]:
            idx = int(parts[0])
            _run(["pactl", "unload-module", str(idx)])
            print(f"  Cleanup: removed virtual source module {idx}")


def _pw_cli_get_node_id(node_name: str) -> Optional[int]:
    """Get the pw-cli global ID for a node by its node.name property."""
    out = _run(["pw-cli", "ls"], timeout=5)
    if not out:
        return None
    lines = out.split("\n")
    for i, line in enumerate(lines):
        if f'node.name = "{node_name}"' in line:
            # Walk backwards to find the "id N," line
            for j in range(i - 1, max(i - 15, -1), -1):
                if lines[j].strip().startswith("id ") and ", type" in lines[j]:
                    # Parse "id 181, type PipeWire:Interface:Node/3"
                    id_part = lines[j].strip().split(",")[0]
                    return int(id_part.replace("id ", ""))
    return None


def destroy_all_pulseforge_sinks():
    """Destroy all PulseForge virtual sink nodes.
    Uses pactl unload-module (reliable) instead of pw-cli destroy (racy).
    Falls back to pw-cli destroy if module unload fails."""
    out = _run(["pactl", "list", "sinks"], timeout=5)
    if not out:
        return
    # Parse full pactl list sinks output to get sink name + owner module
    current_name = None
    current_module = None
    for line in out.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped.split(":")[-1].strip()
            current_module = None
        elif stripped.startswith("Owner Module:"):
            try:
                current_module = int(stripped.split(":")[-1].strip())
            except (ValueError, IndexError):
                current_module = None
        elif stripped.startswith("Sink #") or (current_name and current_module is not None and stripped == ""):
            if current_name and "pulseforge" in current_name and current_module is not None:
                result = _run(["pactl", "unload-module", str(current_module)], timeout=5)
                if result is not None:
                    print(f"  Cleanup: unloaded module {current_module} (sink {current_name})")
                else:
                    node_id = _pw_cli_get_node_id(current_name)
                    if node_id is not None:
                        _run(["pw-cli", "destroy", str(node_id)])
                        print(f"  Cleanup: destroyed sink {current_name} (pw-cli id {node_id})")
                    else:
                        print(f"  Cleanup: could not remove sink {current_name}")
            current_name = None
            current_module = None
    # Handle the last sink if output doesn't end with blank line
    if current_name and "pulseforge" in current_name and current_module is not None:
        result = _run(["pactl", "unload-module", str(current_module)], timeout=5)
        if result is not None:
            print(f"  Cleanup: unloaded module {current_module} (sink {current_name})")
        else:
            node_id = _pw_cli_get_node_id(current_name)
            if node_id is not None:
                _run(["pw-cli", "destroy", str(node_id)])
                print(f"  Cleanup: destroyed sink {current_name} (pw-cli id {node_id})")
            else:
                print(f"  Cleanup: could not remove sink {current_name}")
def destroy_all_pulseforge_sources():
    """Destroy all PulseForge virtual source nodes (e.g. mic.processed)."""
    out = _run(["pactl", "list", "short", "sources"], timeout=5)
    if not out:
        return
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and "pulseforge" in parts[1] and ".monitor" not in parts[1]:
            name = parts[1]
            # Use pw-cli to get the real node ID
            node_id = _pw_cli_get_node_id(name)
            if node_id is not None:
                _run(["pw-cli", "destroy", str(node_id)])
                print(f"  Cleanup: destroyed source {name} (pw-cli id {node_id})")
            else:
                print(f"  Cleanup: could not find pw-cli id for source {name}")


def create_stream_virtual_source():
    """Create a virtual Audio/Source from the stream mix for OBS/streaming apps.
    
    Creates pulseforge_stream_input as an Audio/Source that reads from
    pulseforge_stream.monitor. This makes it appear as a microphone input
    in apps like OBS.
    """
    stream_monitor = "pulseforge_stream.monitor"
    source_name = "pulseforge_stream_input"
    
    # Check if already exists
    existing = get_node_by_name(source_name)
    if existing is not None:
        return existing
    
    result = _run([
        "pactl", "load-module", "module-virtual-source",
        f"source_name={source_name}",
        "source_properties=device.description=PulseForge-Stream-Mic",
        f"master={stream_monitor}",
    ])
    if result:
        import time
        time.sleep(0.3)
        node_id = get_node_by_name(source_name)
        print(f"  Created stream virtual source: {source_name} (node {node_id})")
        return node_id
    return None


def get_sink_monitor_source(sink_name: str) -> Optional[str]:
    """Get the monitor source name for a given sink name.
    
    For PipeWire sinks, the monitor source is typically '<sink_name>.monitor'.
    """
    monitor_name = f"{sink_name}.monitor"
    out = _run(["pactl", "list", "short", "sources"], timeout=5)
    if monitor_name in (out or ""):
        return monitor_name
    return None


def get_sink_name_for_node(node_id: int) -> Optional[str]:
    """Get the pactl Name property for a sink given its node ID.
    
    Uses wpctl inspect to get node.name, which is the pactl Name for virtual sinks.
    """
    return _get_node_name(node_id)


def set_default_sink_by_name(sink_name: str):
    """Set the default sink by its PipeWire node name."""
    node_id = get_node_by_name(sink_name)
    if node_id is not None:
        set_default_sink(node_id)
        return True
    return False


# ─── PipeWire Events ───────────────────────────────────────────────

def subscribe_events():
    """Subscribe to PipeWire events for device/app changes."""
    proc = subprocess.Popen(
        ["pw-cli", "listen"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return proc


# ─── Peak Metering (VU Meters) ─────────────────────────────────────

def get_peak(node_id: int) -> float:
    """Get current peak level for a node (0.0 - 1.0)."""
    try:
        r = subprocess.run(
            ["pw-top", "-n", "-b", "-r", "1"],
            capture_output=True, text=True, timeout=2
        )
        for line in r.stdout.split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[0] == str(node_id):
                for p in reversed(parts):
                    try:
                        val = float(p)
                        if 0.0 <= val <= 2.0:
                            return val
                    except ValueError:
                        continue
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return -1.0
