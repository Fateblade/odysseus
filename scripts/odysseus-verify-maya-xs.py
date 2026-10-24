#!/usr/bin/env python3
"""Verify local XS artifacts against pinned publisher sizes and SHA-256 hashes."""

import argparse
import sys
import json
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.operations.model_artifacts import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_root", type=Path, help="Directory containing Maya-XS/ and dspark/")
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("maya-xs-artifacts.json").read_text(encoding="utf-8"))
    try:
        verify(args.model_root, manifest)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
