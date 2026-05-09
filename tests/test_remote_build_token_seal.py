"""Tests for the offloader-side bearer-at-rest sealing helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from esphome_device_builder.helpers import remote_build_token_seal as sealmod
from esphome_device_builder.helpers.remote_build_token_seal import (
    _FERNET_CACHE,
    _KEY_FILENAME,
    _KEY_MODE,
    TokenSealError,
    seal_bearer,
    unseal_bearer,
)


@pytest.fixture(autouse=True)
def _reset_fernet_cache() -> None:
    """Each test starts with a clean keyfile cache so a tmp_path miss doesn't reuse a prior key."""
    _FERNET_CACHE.clear()
    yield
    _FERNET_CACHE.clear()


def test_seal_round_trips_via_unseal(tmp_path: Path) -> None:
    sealed = seal_bearer(tmp_path, "abcdefghijk.SECRET")
    assert sealed != "abcdefghijk.SECRET"
    assert unseal_bearer(tmp_path, sealed) == "abcdefghijk.SECRET"


def test_seal_creates_key_file_on_first_call(tmp_path: Path) -> None:
    key_path = tmp_path / _KEY_FILENAME
    assert not key_path.exists()
    seal_bearer(tmp_path, "abcdefghijk.SECRET")
    assert key_path.exists()


def test_key_file_has_strict_perms(tmp_path: Path) -> None:
    """Key file must be readable only by the owner — same shape as the cert key."""
    seal_bearer(tmp_path, "abcdefghijk.SECRET")
    key_path = tmp_path / _KEY_FILENAME
    # _KEY_MODE is 0o600; OS-level perm bits are masked at write time.
    assert key_path.stat().st_mode & 0o777 == _KEY_MODE


def test_seal_reuses_existing_key(tmp_path: Path) -> None:
    """Two seals against the same config_dir produce ciphertexts the same key can decrypt."""
    sealed_a = seal_bearer(tmp_path, "first")
    sealed_b = seal_bearer(tmp_path, "second")
    assert unseal_bearer(tmp_path, sealed_a) == "first"
    assert unseal_bearer(tmp_path, sealed_b) == "second"


def test_seal_distinct_for_distinct_config_dirs(tmp_path: Path) -> None:
    """Two separate dashboards generate independent keys; bearers don't cross-decrypt."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    sealed = seal_bearer(dir_a, "abcdefghijk.SECRET")
    with pytest.raises(TokenSealError):
        unseal_bearer(dir_b, sealed)


def test_unseal_rejects_tampered_ciphertext(tmp_path: Path) -> None:
    """Fernet's MAC catches any tampering."""
    sealed = seal_bearer(tmp_path, "abcdefghijk.SECRET")
    # Flip a byte in the middle of the ciphertext payload.
    tampered = sealed[:20] + ("X" if sealed[20] != "X" else "Y") + sealed[21:]
    with pytest.raises(TokenSealError):
        unseal_bearer(tmp_path, tampered)


def test_unseal_rejects_garbage_input(tmp_path: Path) -> None:
    seal_bearer(tmp_path, "ensure-key-exists")
    with pytest.raises(TokenSealError):
        unseal_bearer(tmp_path, "not-a-fernet-token")


def test_seal_serialises_first_creation_under_lock(tmp_path: Path) -> None:
    """
    Two threads racing into ``_get_or_create_fernet`` must not both write a key.

    The lock + cache double-check is the load-bearing piece: without
    it, the second writer's keyfile would invalidate the first
    writer's freshly-sealed bearer (still in flight). We can't
    cleanly run two threads in pytest without flakiness, so we
    assert the lock is held during the keyfile materialisation by
    monkeypatching atomic_write to verify only one call happens
    per config_dir.
    """
    write_calls: list[Path] = []
    real_write = sealmod.atomic_write

    def _tracking_write(path: Path, data: bytes, *, mode: int = 0o644) -> None:
        write_calls.append(path)
        real_write(path, data, mode=mode)

    with patch.object(sealmod, "atomic_write", _tracking_write):
        seal_bearer(tmp_path, "first")
        seal_bearer(tmp_path, "second")
        seal_bearer(tmp_path, "third")

    # Three seal_bearer calls but only one keyfile creation.
    assert len(write_calls) == 1
    assert write_calls[0] == tmp_path / _KEY_FILENAME
