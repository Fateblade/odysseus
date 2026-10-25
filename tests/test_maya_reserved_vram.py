"""Guard the pinned GLM patch and exercise reserved-memory budgeting."""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    spec = importlib.util.spec_from_file_location("maya_vram_patch", ROOT / "scripts/odysseus-patch-maya-glm-vram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_glm_source_is_rejected():
    with pytest.raises(ValueError, match="differs from pinned"):
        helper().patch_source(b"unrelated source\n")


def test_patch_is_idempotent_preserves_backup_and_rejects_modified_header(tmp_path, monkeypatch):
    module = helper()
    raw = (module.INCLUDE + module.ANCHOR + '\n').encode()
    monkeypatch.setattr(module, "SOURCE_SHA256", hashlib.sha256(raw).hexdigest())
    patched = module.patch_source(raw)
    assert module.patch_source(patched) == patched
    with pytest.raises(ValueError, match="differs from pinned"):
        module.patch_source(patched + b"unrelated edit\n")
    with pytest.raises(ValueError, match="Partial or modified"):
        module.patch_source(raw.replace(module.INCLUDE.encode(), (module.INCLUDE + module.EXTRA_INCLUDE).encode()))
    source = tmp_path / "src/core/glm_fast_path.cu"
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    monkeypatch.setattr(sys, "argv", ["patch", str(tmp_path)])
    module.main()
    module.main()
    assert source.read_bytes() == patched
    assert source.with_name("glm_fast_path.cu.before-odysseus-vram").read_bytes() == raw
    source.with_name("odysseus_glm_reserved_vram.hpp").write_text("unrelated", encoding="utf-8")
    with pytest.raises(SystemExit):
        module.main()
    assert source.read_bytes() == patched


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is needed for the standalone C++ memory-budget test")
def test_cpp_reserved_memory_budget(tmp_path):
    executable = tmp_path / "reserved-vram-test"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "scripts"),
                    str(ROOT / "tests/maya_reserved_vram_test.cpp"), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is needed for the C++ patch compile check")
def test_injected_cpp_compiles_and_fails_closed_without_sysfs(tmp_path):
    module = helper()
    source = tmp_path / "planner.cpp"
    source.write_text('''#include "maya-glm-reserved-vram.hpp"
#include <fstream>
#include <string>
#include <cctype>
#include <cstdio>
#include <cstdlib>
constexpr int cudaSuccess = 0;
int cudaDeviceGetPCIBusId(char*, int, int) { return 1; }
size_t mmq_scratch_reserve() { return 0; }
bool planner() {
    std::string err;
    int dev_ = 0;
    size_t avail = 0, free_b = 0, reserve = 0, expert_bytes = 0;
    long long avail_kb = 0;
    double head_gb = 16;
    void* pf_ = nullptr;
''' + module.INSERT + '''
    (void) avail;
    return true;
}
int main() { return planner() ? 0 : 1; }
''', encoding="utf-8")
    executable = tmp_path / "planner-test"
    subprocess.run(["g++", "-std=c++17", "-D__linux__", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "scripts"),
                    str(source), "-o", str(executable)], check=True)
    for mode, expected in (("0", 0), ("1", 1), ("invalid", 1)):
        result = subprocess.run([str(executable)], env={**os.environ, "STRATA_GLM_RESERVED_VRAM": mode})
        assert result.returncode == expected
