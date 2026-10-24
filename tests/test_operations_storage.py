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



def test_storage_totals_count_repeated_filesystem_sources_once():
    from services.operations.overview import storage_totals
    rows = [{"source": "/dev/nvme0n1p2", "total_bytes": 100, "used_bytes": 60, "available_bytes": 35},
            {"source": "/dev/nvme0n1p2", "total_bytes": 100, "used_bytes": 60, "available_bytes": 35},
            {"source": "/dev/nvme1n1p1", "total_bytes": 200, "used_bytes": 100, "available_bytes": 90}]
    assert storage_totals(rows) == {"total_bytes": 300, "used_bytes": 160, "available_bytes": 125}


def test_resource_sample_reads_only_host_and_storage(monkeypatch):
    from services.operations import overview
    calls = []
    import src.host_docker_access
    monkeypatch.setattr(overview.platform, "system", lambda: "Linux")
    monkeypatch.setattr(src.host_docker_access, "running_in_container", lambda: False)
    monkeypatch.setattr(overview, "host_metrics", lambda capacity: (calls.append(("host", capacity)), {"status": "ok"})[1])
    monkeypatch.setattr(overview, "storage", lambda: (calls.append(("storage",)), {"status": "ok"})[1])
    def unexpected(*args, **kwargs):
        raise AssertionError("Resource sampling must not query inference services or processes")
    monkeypatch.setattr(overview, "containers", unexpected)
    monkeypatch.setattr(overview, "strata", unexpected)
    monkeypatch.setattr(overview, "ollama", unexpected)
    result = overview.resources(128)
    assert result["configured"] is True and result["sampled_at"] > 0
    assert result["host"]["status"] == "ok" and result["storage"]["status"] == "ok"
    assert sorted(calls) == [("host", 128), ("storage",)]



def test_resource_sample_does_not_substitute_container_disks(monkeypatch):
    from services.operations import overview
    import src.host_docker_access
    monkeypatch.setattr(overview.platform, "system", lambda: "Linux")
    monkeypatch.setattr(src.host_docker_access, "running_in_container", lambda: True)
    def unexpected():
        raise AssertionError("Do not sample container disks as host disks")
    monkeypatch.setattr(overview, "storage", unexpected)
    assert overview.resources()["configured"] is False
