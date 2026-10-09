#!/bin/bash
# PulseForge launcher
# Resolve symlink to find real script dir
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

# PulseForge spawns many short-lived threads/subprocesses (VU monitors,
# channel recorders). glibc otherwise creates a malloc arena per thread and
# never returns the memory to the OS; capping arenas stops the multi-GB
# RSS blowup and swap growth seen after the app runs for a while.
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

exec python3 -m pulseforge.main "$@"
