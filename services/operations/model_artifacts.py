"""Verify immutable local model artifacts against reviewed publisher metadata."""

import hashlib
import re
from pathlib import Path


def verify(root, manifest):
    root = Path(root).resolve()
    for name, expected in manifest["files"].items():
        path = root / name
        if Path(name).is_absolute() or not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError(f"Artifact must stay inside the model root: {name}")
        if (type(expected.get("bytes")) is not int or expected["bytes"] < 1
                or not re.fullmatch(r"[0-9a-f]{64}", str(expected.get("sha256", "")))):
            raise ValueError(f"Invalid pinned artifact metadata: {name}")
        if not path.is_file() or path.stat().st_size != expected["bytes"]:
            raise ValueError(f"Missing or incorrect size: {name}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected["sha256"]:
            raise ValueError(f"SHA-256 mismatch: {name}")
        print(f"Verified {name}", flush=True)
