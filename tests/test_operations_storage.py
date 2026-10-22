"""Disk space samples exclude memory-backed filesystems and tolerate failures."""

import subprocess
from types import SimpleNamespace

from services.operations.overview import storage


def test_disk_space_preserves_mount_names_and_available_bytes(monkeypatch):
    def run(command, **kwargs):
        assert "--local" in command
        assert kwargs["timeout"] == 5
        return SimpleNamespace(returncode=0, stdout="Filesystem Type 1B-blocks Used Avail Use% Mounted on\n/dev/nvme0 ext4 10000 7000 2000 78% /\ntmpfs tmpfs 10000 1000 9000 10% /run\n/dev/nvme1 xfs 20000 18000 2000 90% /mnt/AI Models\n")
    monkeypatch.setattr(subprocess, "run", run)
    result = storage()
    assert result["status"] == "ok"
    assert len(result["items"]) == 2
    assert result["items"][0]["available_bytes"] == 2000
    assert result["items"][1]["mount"] == "/mnt/AI Models"
    assert result["items"][1]["used_percent"] == 90


def test_disk_space_failure_is_not_reported_as_empty_disks(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("df", 5)
    monkeypatch.setattr(subprocess, "run", run)
    result = storage()
    assert result["status"] == "unavailable"
    assert result["items"] == []
