import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def diagnostic():
    path = Path(__file__).resolve().parents[1] / "scripts/odysseus-diagnose-strata-ds41f.py"
    spec = importlib.util.spec_from_file_location("strata_diagnostic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", [None, "build", "timeout"])
def test_restores_source_and_binary_without_qualifying(tmp_path, monkeypatch, diagnostic, failure):
    source = tmp_path / diagnostic.SOURCE
    source.parent.mkdir(parents=True)
    original = (diagnostic.ANCHOR + "\n").encode()
    source.write_bytes(original)
    receipt = tmp_path / "build-hip/odysseus-build-receipt.json"
    receipt.parent.mkdir()
    receipt.write_bytes(b"unchanged")
    calls = []
    monkeypatch.setattr(diagnostic.subprocess, "check_output", lambda command, **kwargs: diagnostic.REVISION if kwargs.get("text") else original)

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            assert b"DIAG same WMMA h" in source.read_bytes()
            if failure == "build":
                raise subprocess.CalledProcessError(2, command)
        elif "--build" in command:
            assert source.read_bytes() == original
        elif failure == "timeout":
            raise subprocess.TimeoutExpired(command, 600)
        return subprocess.CompletedProcess(command, 1 if "--build" not in command else 0)

    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    if failure:
        with pytest.raises(subprocess.SubprocessError):
            diagnostic.diagnose(tmp_path)
    else:
        assert diagnostic.diagnose(tmp_path) == 1
        assert calls[2][-2:] == ["--type", "23"]
    assert source.read_bytes() == original
    assert "--build" in calls[-1]
    assert receipt.read_bytes() == b"unchanged"
    assert all("strata-dsv41" not in command for command in calls)


def test_refuses_local_changes_before_build(tmp_path, monkeypatch, diagnostic):
    source = tmp_path / diagnostic.SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes(b"local work")
    monkeypatch.setattr(diagnostic.subprocess, "check_output", lambda command, **kwargs: diagnostic.REVISION if kwargs.get("text") else b"pristine")
    with pytest.raises(ValueError, match="local changes"):
        diagnostic.diagnose(tmp_path)
    assert source.read_bytes() == b"local work"


def test_keeps_original_gate_and_requires_forced_paths(diagnostic):
    patched = diagnostic.instrument(diagnostic.ANCHOR.encode()).decode()
    assert patched.count(diagnostic.ANCHOR) == 1
    assert "cross_m.xh = sh" in patched and "cross_s.xh = mh" in patched
    assert "cross_taken < 1 || scalar_taken != 0" in patched
    with pytest.raises(ValueError, match="insertion point"):
        diagnostic.instrument(b"changed upstream test")
