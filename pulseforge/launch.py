#!/usr/bin/env python3
"""PulseForge launch script."""
import sys
import os

# Add project to path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(script_dir))

from pulseforge.main import main

if __name__ == "__main__":
    main()
