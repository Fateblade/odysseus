#!/usr/bin/env python3
"""Configure and build the pinned gfx1151 GLM HIP target with the validated Ubuntu flags."""

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

LLAMA_REVISION = "3cf03257f219afbe7334045ff7c6a06ac68c627d"
ROCWMMA_REVISION = "27a847f610bfdb4e58dd257889734168b0ef8626"


def git_revision(root):
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def configuration(root, headers, bitcode):
    return [str(root / ".venv/bin/cmake"), "-S", str(root), "-B", str(root / "build-hip"), "-G", "Unix Makefiles",
            "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_HIP_FLAGS_RELEASE:STRING=-O3 -DNDEBUG",
            f"-DCMAKE_HIP_FLAGS:STRING=--rocm-path=/usr --rocm-device-lib-path={bitcode} -I{headers}",
            "-DSTRATA_ENABLE_HIP=ON", "-DSTRATA_ENABLE_CUDA=OFF", "-DSTRATA_PREFILL_MMQ=ON",
            "-DSTRATA_NATIVE_EXPERTS=ON", "-DSTRATA_BUILD_TESTS=OFF", "-DCMAKE_HIP_ARCHITECTURES=gfx1151",
            "-DCMAKE_HIP_COMPILER=/usr/lib/llvm-21/bin/clang++", "-DCMAKE_HIP_COMPILER_ROCM_ROOT=/usr",
            "-DCMAKE_PREFIX_PATH=/usr", f"-DSTRATA_GGML_DIR={root / 'third_party/llama.cpp'}"]


def verify_release_flags(build):
    text = (build / "CMakeFiles/strata_kernels.dir/flags.make").read_text()
    flags = next((line.partition("=")[2] for line in text.splitlines() if line.startswith("HIP_FLAGS =")), "")
    options = [arg for arg in shlex.split(flags) if re.fullmatch(r"-O(?:[0-3sgz]|fast)", arg)]
    if not options or options[-1] != "-O3" or "-DNDEBUG" not in flags:
        raise ValueError("Generated HIP kernel flags are not optimized Release flags; inspect the build before continuing.")
    print("Verified actual strata_kernels HIP flags: -O3 and -DNDEBUG", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--rocwmma-include", type=Path, default=Path.home() / "rocwmma-7.1.0/library/include")
    parser.add_argument("--device-lib-dir", type=Path, default=Path("/usr/lib/llvm-21/lib/clang/21/amdgcn/bitcode"))
    parser.add_argument("--jobs", type=int, default=1, choices=range(1, 5))
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run this build helper on the Ubuntu gfx1151 host.")
    root, headers, bitcode = (path.expanduser().resolve() for path in (args.directory, args.rocwmma_include, args.device_lib_dir))
    manifest = json.loads(Path(__file__).with_name("maya-glm53-artifacts.json").read_text())
    try:
        if git_revision(root) != manifest["engine_revision"] or git_revision(root / "third_party/llama.cpp") != LLAMA_REVISION:
            raise ValueError("Maya or llama.cpp does not match the reviewed source pins.")
        if git_revision(headers.parents[1]) != ROCWMMA_REVISION:
            raise ValueError("rocWMMA headers do not match the reviewed 7.1.0 pin.")
        for file in (headers / "rocwmma/internal/accessors.hpp", bitcode / "ocml.bc", root / ".venv/bin/cmake", Path("/usr/lib/llvm-21/bin/clang++")):
            if not file.is_file():
                raise ValueError(f"Missing installed build prerequisite: {file}")
        if any(char.isspace() for char in str(headers) + str(bitcode)):
            raise ValueError("Use header and bitcode paths without whitespace for HIP compiler flags.")
        subprocess.run(configuration(root, headers, bitcode), check=True)
        verify_release_flags(root / "build-hip")
        subprocess.run([str(root / ".venv/bin/cmake"), "--build", str(root / "build-hip"), "--target", "maya", "-j", str(args.jobs)], check=True)
        print(f"Built GLM engine: {root / 'build-hip/maya'}", flush=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
