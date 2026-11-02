#!/usr/bin/env python3
"""Experimental gfx1151 build gate: no model downloads or service changes."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REVISION = "c62d5e3a15b40ea51c96aae9e8d36928965f53ab"
CHECKS = ["ds4_iq_test", "ds4_tq_test", "ds4_tq_test_portable"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on the Ubuntu gfx1151 host using the existing ROCm stack.")
    root = args.directory.expanduser().resolve()
    spec = importlib.util.spec_from_file_location("maya_build", Path(__file__).with_name("odysseus-build-maya-glm.py"))
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    headers = Path.home() / "rocwmma-7.1.0/library/include"
    bitcode = Path("/usr/lib/llvm-21/lib/clang/21/amdgcn/bitcode")
    receipt = root / "build-hip/odysseus-build-receipt.json"
    try:
        if build.git_revision(root) != REVISION or build.git_revision(root / "third_party/llama.cpp") != build.LLAMA_REVISION or build.git_revision(headers.parents[1]) != build.ROCWMMA_REVISION:
            raise ValueError("Source or dependency revision differs from the reviewed build contract.")
        for file in (headers / "rocwmma/internal/accessors.hpp", bitcode / "ocml.bc", root / ".venv/bin/cmake", Path("/usr/lib/llvm-21/bin/clang++")):
            if not file.is_file():
                raise ValueError(f"Missing existing build prerequisite: {file}")
        receipt.unlink(missing_ok=True)
        command = [item.replace("-DSTRATA_BUILD_TESTS=OFF", "-DSTRATA_BUILD_TESTS=ON") for item in build.configuration(root, headers, bitcode)]
        command.append("-DSTRATA_BUILD_DSV41=ON")
        subprocess.run(command, check=True)
        for target in ("strata-dsv41", "strata_dsv41_kernels"):
            build.verify_release_flags(root / "build-hip", target)
        subprocess.run([str(root / ".venv/bin/cmake"), "--build", str(root / "build-hip"), "--target", "strata-dsv41", *CHECKS, "-j", str(args.jobs)], check=True)
        executable = root / "build-hip/strata-dsv41"
        subprocess.run([str(executable), "--version"], check=True, timeout=30)
        for name in CHECKS:
            subprocess.run([str(root / "build-hip" / name)], check=True, timeout=600)
        with executable.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        receipt.write_text(json.dumps({"source_revision": REVISION, "binary_sha256": digest, "kernel_checks": CHECKS}, indent=2), encoding="utf-8")
        receipt.chmod(0o600)
        print(f"Build/kernel checks passed; receipt: {receipt}. Model/runtime validation is still required.")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
