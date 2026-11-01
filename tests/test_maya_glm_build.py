import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("glm_build", ROOT / "scripts/odysseus-build-maya-glm.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_build_configuration_preserves_all_proven_hip_flags(tmp_path):
    command = build.configuration(tmp_path, tmp_path / "headers", tmp_path / "bitcode")
    assert "-DCMAKE_HIP_FLAGS_RELEASE:STRING=-O3 -DNDEBUG" in command
    assert any("--rocm-path=/usr --rocm-device-lib-path=" in item and " -I" in item for item in command)
    assert "-DCMAKE_HIP_ARCHITECTURES=gfx1151" in command
    assert "-DSTRATA_ENABLE_CUDA=OFF" in command


def test_actual_hip_flags_must_end_with_release_optimization(tmp_path):
    folder = tmp_path / "CMakeFiles/strata_kernels.dir"; folder.mkdir(parents=True)
    file = folder / "flags.make"
    file.write_text("HIP_FLAGS = --offload-arch=gfx1151 -O3 -DNDEBUG\n")
    build.verify_release_flags(tmp_path)
    file.write_text("HIP_FLAGS = --offload-arch=gfx1151 -O3 -DNDEBUG -O0\n")
    with pytest.raises(ValueError, match="not optimized"):
        build.verify_release_flags(tmp_path)


def test_candidate_build_uses_only_reviewed_ds41f_target(tmp_path, monkeypatch):
    import sys
    root = tmp_path / "candidate"
    headers = tmp_path / "rocwmma/library/include"
    def revision(path):
        if path == root:
            return build.CANDIDATE_REVISION
        if path == root / "third_party/llama.cpp":
            return build.LLAMA_REVISION
        return build.ROCWMMA_REVISION
    commands = []
    monkeypatch.setattr(build, "git_revision", revision)
    monkeypatch.setattr(build, "verify_release_flags", lambda path, *args: None)
    monkeypatch.setattr(build.subprocess, "run", lambda args, **kwargs: commands.append(args))
    monkeypatch.setattr(Path, "is_file", lambda path: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["build", str(root), "--rocwmma-include", str(headers), "--candidate-release", "v1.1.48"])
    build.main()
    assert commands[-1][commands[-1].index("--target") + 1] == "maya-ds4"
    assert "-DCMAKE_HIP_FLAGS_RELEASE:STRING=-O3 -DNDEBUG" in commands[0]
