#!/usr/bin/env python3
"""Verify local XS artifacts against pinned publisher sizes and SHA-256 hashes."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(root, manifest):
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file() or path.stat().st_size != expected["bytes"]:
            raise ValueError(f"Missing or incorrect size: {name}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {name}")
        print(f"Verified {name}", flush=True)


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
