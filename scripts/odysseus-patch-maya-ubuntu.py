#!/usr/bin/env python3
"""Adapt pinned Maya v1.1.22 Ubuntu detection and explicitly optimize HIP Release builds."""

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
    ('conf += ["-S", str(ROOT), "-B", str(BUILD), "-DCMAKE_BUILD_TYPE=Release",\n'
     '             "-DSTRATA_ENABLE_HIP=ON",',
     'conf += ["-S", str(ROOT), "-B", str(BUILD), "-DCMAKE_BUILD_TYPE=Release",\n'
     '             "-DCMAKE_HIP_FLAGS_RELEASE:STRING=-O3 -DNDEBUG",\n'
     '             "-DSTRATA_ENABLE_HIP=ON",'),
)


def patch_source(raw):
    source = raw.replace(b"\r\n", b"\n").decode("utf-8")
    original, patched = source, source
    for old, new in REPLACEMENTS:
        if source.count(new) == 1:
            original = original.replace(new, old)
        elif source.count(old) == 1:
            patched = patched.replace(old, new)
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
        print("Ubuntu layout and HIP Release optimization patch already applied.")
        return
    backup = path.with_name("maya.py.before-ubuntu-layout")
    if backup.exists():
        saved = backup.read_bytes().replace(b"\r\n", b"\n")
        if backup.is_symlink() or hashlib.sha256(saved).hexdigest() != SOURCE_SHA256:
            parser.error("Existing backup differs from pinned original; preserve and inspect it first.")
    else:
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
    print(f"Patched Ubuntu detection and HIP Release optimization. Original saved at {backup}")
    print('Existing builds also need CMake configuration with -DCMAKE_HIP_FLAGS_RELEASE:STRING="-O3 -DNDEBUG" and rebuilding.')


if __name__ == "__main__":
    main()
