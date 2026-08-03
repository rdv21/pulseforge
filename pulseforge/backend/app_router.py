"""App routing — detect apps and route to groups, with persistence.

Uses pactl sink-inputs (not wpctl streams) for reliable app identification
and correct sink-input IDs for pactl move-sink-input.
"""
import json
from typing import Optional
from . import pipewire_ctl as pw
from .pipewire_ctl import _run
from . import config


def get_app_group(app_key: str) -> str:
    """Get the group for an app. Returns group name or DEFAULT_GROUP if unassigned."""
    routing = config.load_routing()
    if app_key in routing:
        return routing[app_key]
    return pw.DEFAULT_GROUP


def move_app_to_group(sink_input_id: int, app_key: str, group: str):
    """Move an app's audio stream to a group sink and persist the assignment.
    
    Args:
        sink_input_id: pactl sink-input index (from pactl list sink-inputs)
        app_key: stable app identifier (binary name or app name) for persistence
        group: target group name (game, chat, media, aux, gaming, stream)
    """
    # Get the pactl sink index for this group
    sink_name = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
    sink_index = pw.get_sink_index_by_name(sink_name)

    if sink_index is not None:
        pw.move_stream_to_sink(sink_input_id, sink_index)

    # Persist the assignment
    routing = config.load_routing()
    routing[app_key] = group
    config.save_routing(routing)


def get_apps_for_group(group: str) -> list[pw.AudioNode]:
    """Get all apps currently routed to a group."""
    all_apps = list_all_apps()
    return [app for app, g in all_apps if g == group]


def auto_route_new_apps():
    """Check for new apps and route them to their saved group (or DEFAULT_GROUP).
    
    Uses pactl sink-inputs for reliable app identification and routing.
    """
    apps = pw.list_sink_inputs()
    routing = config.load_routing()

    for app in apps:
        # Use binary name as the routing key (most stable)
        app_key = app.name  # from application.process.binary
        if not app_key:
            app_key = app.description  # fallback to app name

        group = routing.get(app_key, pw.DEFAULT_GROUP)
        sink_name = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
        sink_index = pw.get_sink_index_by_name(sink_name)

        if sink_index is not None:
            # Try to move — PipeWire will no-op if already there
            pw.move_stream_to_sink(app.id, sink_index)


def route_new_apps_only():
    """Route only apps that aren't on any PulseForge sink yet.
    
    This won't fight manual moves or module-stream-restore — it only
    picks up apps that just launched and are still on a hardware/default sink.
    """
    apps = pw.list_sink_inputs()
    routing = config.load_routing()
    
    # Get all PulseForge sink pactl indices
    pf_sink_indices = set()
    out = _run(["pactl", "list", "short", "sinks"], timeout=5)
    for line in out.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and "pulseforge" in parts[1]:
            pf_sink_indices.add(int(parts[0]))
    
    for app in apps:
        app_key = app.name
        if not app_key:
            app_key = app.description
        
        # Check which sink the app is currently on
        current_sink_id = pw.get_stream_sink_id(app.id)
        if current_sink_id is not None and current_sink_id in pf_sink_indices:
            # Already on a PulseForge sink — skip (don't fight manual moves)
            continue
        
        # App is on a hardware/default sink — route it to its group
        group = routing.get(app_key, pw.DEFAULT_GROUP)
        sink_name = pw._VIRTUAL_SINK_INTERNAL.get(group, group)
        sink_index = pw.get_sink_index_by_name(sink_name)
        
        if sink_index is not None:
            pw.move_stream_to_sink(app.id, sink_index)
            print(f"  Auto-route: {app_key} → {group}")


def list_all_apps() -> list[tuple[pw.AudioNode, str]]:
    """List all apps and their current group. Returns (app, group) tuples.
    
    Uses pactl list-sink-inputs for reliable app identification.
    The app.id is the pactl sink-input index (correct for move-sink-input).
    The app.name is the application.process.binary (stable across restarts).
    """
    apps = pw.list_sink_inputs()
    routing = config.load_routing()

    result = []
    for app in apps:
        # Use binary name as routing key
        app_key = app.name  # from application.process.binary
        if not app_key:
            app_key = app.description

        group = routing.get(app_key, pw.DEFAULT_GROUP)
        result.append((app, group))

    return result
