"""Guard the isolated upstream patch and exercise its compiler discovery."""

import ast
import hashlib
import importlib.util
import sys
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
def configure():
    ROOT = BUILD = Path(".")
    conf = []
    conf += ["-S", str(ROOT), "-B", str(BUILD), "-DCMAKE_BUILD_TYPE=Release",
             "-DSTRATA_ENABLE_HIP=ON", "-DSTRATA_ENABLE_CUDA=OFF"]
    return conf
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
    assert "-DCMAKE_HIP_FLAGS_RELEASE:STRING=-O3 -DNDEBUG" in namespace["configure"]()
    earlier = raw
    for old, new in module.REPLACEMENTS[:2]:
        earlier = earlier.replace(old.encode(), new.encode())
    assert module.patch_source(earlier) == patched
    checkout = tmp_path / "maya"
    checkout.mkdir()
    target = checkout / "maya.py"
    target.write_bytes(earlier)
    backup = checkout / "maya.py.before-ubuntu-layout"
    backup.write_bytes(raw)
    actual_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: True if self.as_posix().startswith('/usr/') else actual_is_file(self))
    monkeypatch.setattr(sys, "argv", ["patch", str(checkout)])
    module.main()
    module.main()
    assert target.read_bytes() == patched
    assert backup.read_bytes() == raw
    with pytest.raises(ValueError, match="does not match pinned"):
        module.patch_source(raw + b"print('unrelated edit')\n")
