#!/usr/bin/env python3
"""Verify ordinary GLM Maya-S-v2 shards against pinned publisher sizes and SHA-256."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.operations.model_artifacts import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_root", type=Path, help="Directory containing Maya-S-v2-IQ2_XXS/")
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("maya-glm53-artifacts.json").read_text(encoding="utf-8"))
    try:
        verify(args.model_root, manifest)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
