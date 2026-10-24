#!/usr/bin/env python3
"""Run the shared text and fixed-tool acceptance checks for a native model endpoint."""

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("odysseus-evaluate-deepseek.py")))["main"]()
