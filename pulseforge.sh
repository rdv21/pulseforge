#!/bin/bash
# PulseForge launcher
# Automatically sets up NVIDIA AFX library path if SDK is detected
# Resolve symlink to find real script dir
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$SCRIPT_DIR/.."
export PYTHONPATH="$SCRIPT_DIR/..:${PYTHONPATH:-}"

AFX_SDK="${AFX_SDK_ROOT:-$HOME/.local/share/linux-broadcast/nvidia/current}"

if [ -d "$AFX_SDK/external/cuda/lib" ]; then
  export LD_LIBRARY_PATH="$AFX_SDK/external/cuda/lib:$AFX_SDK/features/denoiser/lib:$AFX_SDK/features/dereverb/lib:$AFX_SDK/features/dereverb_denoiser/lib:$AFX_SDK/features/studio_voice/lib:$AFX_SDK/nvafx/lib:${LD_LIBRARY_PATH:-}"
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

exec python3 -m pulseforge.main "$@"
