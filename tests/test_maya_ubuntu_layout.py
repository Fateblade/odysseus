"""Guard the isolated upstream patch and exercise its compiler discovery."""

import ast
import hashlib
import importlib.util
from pathlib import Path

import pytest


def helper():
    path = Path(__file__).resolve().parents[1] / "scripts/odysseus-patch-maya-ubuntu.py"
    spec = importlib.util.spec_from_file_location("maya_ubuntu_patch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_source_is_never_patched():
    with pytest.raises(ValueError, match="refusing to patch"):
        helper().patch_source(b"print('unrelated')\n")


def test_patch_finds_ubuntu_compiler_and_library_without_system_links(tmp_path, monkeypatch):
    module = helper()
    raw = b'''from pathlib import Path
def hip_clang(root):
    exe = "clang++"
    return next((p for p in (root / "llvm" / "bin" / exe, root / "lib" / "llvm" / "bin" / exe,
                             root / "lib64" / "rocm" / "llvm" / "bin" / exe, root / "bin" / exe)
                 if p.exists()), None)
def library_found(root):
    return any(list((root / d).glob("libhipblas.so*")) for d in ("lib", "lib64"))
'''
    monkeypatch.setattr(module, "SOURCE_SHA256", hashlib.sha256(raw).hexdigest())
    patched = module.patch_source(raw)
    assert module.patch_source(patched) == patched
    namespace = {}
    exec(compile(ast.parse(patched), "patched-fixture", "exec"), namespace)
    compiler = tmp_path / "lib/llvm-21/bin/clang++"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    library = tmp_path / "lib/x86_64-linux-gnu/libhipblas.so"
    library.parent.mkdir(parents=True)
    library.touch()
    assert namespace["hip_clang"](tmp_path) == compiler
    assert namespace["library_found"](tmp_path) is True
    with pytest.raises(ValueError, match="does not match pinned"):
        module.patch_source(raw + b"print('unrelated edit')\n")
