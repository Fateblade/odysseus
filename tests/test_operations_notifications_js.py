"""Run Operations notification lifetime and dismissal regressions."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node binary not on PATH")
def test_notification_lifetimes_and_dismissal():
    result = subprocess.run(["node", "--test", "tests/operationsNotifications.test.mjs"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
