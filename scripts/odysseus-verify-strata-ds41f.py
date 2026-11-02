#!/usr/bin/env python3
"""Verify selected immutable IST-DAS model/support files after upstream setup."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    manifest = json.loads(Path(__file__).with_name("strata-ds41f-artifacts.json").read_text(encoding="utf-8"))
    try:
        for entry in manifest["files"]:
            matches = [file for file in root.rglob(Path(entry["path"]).name) if file.is_file() and not file.is_symlink()]
            if len(matches) != 1:
                raise ValueError("Need exactly one immutable source file: " + entry["path"])
            file = matches[0]
            if not file.resolve().is_relative_to(root) or file.stat().st_size != entry["bytes"]:
                raise ValueError("Source file size/path mismatch: " + entry["path"])
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != entry["sha256"]:
                raise ValueError("Source file SHA256 mismatch: " + entry["path"])
            print("Verified: " + entry["path"])
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
