#!/bin/bash
# PulseForge launcher
cd "$(dirname "$0")"
exec python3 -m pulseforge.main "$@"
