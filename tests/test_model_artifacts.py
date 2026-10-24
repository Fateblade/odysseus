import hashlib

import pytest

from services.operations.model_artifacts import verify


def test_verify_checks_size_and_hash_without_modifying_files(tmp_path):
    path = tmp_path / "tiny.gguf"; path.write_bytes(b"fixture")
    manifest = {"files": {path.name: {"bytes": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}}}
    verify(tmp_path, manifest)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify(tmp_path, manifest)
    path.write_bytes(b"short")
    with pytest.raises(ValueError, match="incorrect size"):
        verify(tmp_path, manifest)


def test_verify_rejects_paths_outside_model_root(tmp_path):
    manifest = {"files": {"../outside.gguf": {"bytes": 1, "sha256": "a" * 64}}}
    with pytest.raises(ValueError, match="inside the model root"):
        verify(tmp_path, manifest)
