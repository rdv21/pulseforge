#!/bin/bash
# PulseForge launcher
# Automatically sets up NVIDIA AFX library path if SDK is detected
cd "$(dirname "$0")"

AFX_SDK="${AFX_SDK_ROOT:-$HOME/.local/share/linux-broadcast/nvidia/current}"

if [ -d "$AFX_SDK/external/cuda/lib" ]; then
  export LD_LIBRARY_PATH="$AFX_SDK/external/cuda/lib:$AFX_SDK/features/denoiser/lib:$AFX_SDK/features/dereverb/lib:$AFX_SDK/features/dereverb_denoiser/lib:$AFX_SDK/features/studio_voice/lib:$AFX_SDK/nvafx/lib:${LD_LIBRARY_PATH:-}"
fi

export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

exec python3 -m pulseforge.main "$@"
