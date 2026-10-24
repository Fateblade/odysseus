#!/usr/bin/env python3
"""Adapt pinned Maya v1.1.22 detection to Ubuntu LLVM 21 and x86_64 hipBLAS."""

import argparse
import hashlib
import os
from pathlib import Path


SOURCE_SHA256 = "76a2538acd0b810ee7b159b3a25c801ef869997118f3d038e1f71ec81e3ade4d"
REPLACEMENTS = (
    ('root / "lib64" / "rocm" / "llvm" / "bin" / exe, root / "bin" / exe)',
     'root / "lib64" / "rocm" / "llvm" / "bin" / exe, root / "lib" / "llvm-21" / "bin" / exe, root / "bin" / exe)'),
    ('(root / d).glob("libhipblas.so*")) for d in ("lib", "lib64"))',
     '(root / d).glob("libhipblas.so*")) for d in ("lib", "lib64", "lib/x86_64-linux-gnu"))'),
)


def patch_source(raw):
    source = raw.replace(b"\r\n", b"\n").decode("utf-8")
    original, patched = source, source
    for old, new in REPLACEMENTS:
        if source.count(old) == 1 and new not in source:
            patched = patched.replace(old, new)
        elif source.count(new) == 1 and old not in source:
            original = original.replace(new, old)
        else:
            raise ValueError("Unexpected Maya detection code; refusing to patch.")
    if hashlib.sha256(original.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError("maya.py does not match pinned v1.1.22 source; preserve edits and inspect it first.")
    return patched.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Isolated project-maya-xs checkout")
    args = parser.parse_args()
    for name in ("/usr/bin/hipcc", "/usr/lib/llvm-21/bin/clang++", "/usr/lib/x86_64-linux-gnu/libhipblas.so"):
        if not Path(name).is_file():
            parser.error(f"Expected installed Ubuntu prerequisite is missing: {name}")
    path = args.directory.expanduser().resolve() / "maya.py"
    if path.is_symlink():
        parser.error("maya.py must not be a symlink")
    raw = path.read_bytes()
    try:
        patched = patch_source(raw)
    except ValueError as error:
        parser.error(str(error))
    if patched == raw.replace(b"\r\n", b"\n"):
        print("Ubuntu layout patch already applied.")
        return
    backup = path.with_name("maya.py.before-ubuntu-layout")
    with backup.open("xb") as file:
        file.write(raw)
    temporary = path.with_name("maya.py.ubuntu-layout.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(patched)
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Patched Ubuntu detection only. Original saved at {backup}")


if __name__ == "__main__":
    main()
