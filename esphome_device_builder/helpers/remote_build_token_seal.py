"""
Symmetric encryption for offloader-side bearer tokens at rest.

Phase 4a stores receiver bearers on the offloader so the
peer-link layer (phase 5+) can authenticate against the
receiver. Unlike receiver-side tokens (stored as ``secret_sha256``
in :class:`StoredToken` because the receiver only verifies),
the offloader needs the *cleartext* to present the bearer, so
hashing isn't an option. Storing cleartext is the obvious
alternative; this module is the better one.

Threat model: a backup snapshot of ``.device-builder.json`` (or
the equivalent ``/data/`` dir on HA addon) leaks every bearer in
cleartext. With a separate 0o600 keyfile that lives outside the
backup-able config tree (or simply isn't included in routine
config-only backups), the leak is confined to the metadata file
alone — useless without the key.

Primitive: ``cryptography.fernet.Fernet`` (AES-128-CBC + HMAC-
SHA-256). Not the most modern AEAD on offer, but well-vetted,
tamper-evident, and the canonical "give me a symmetric envelope"
helper from the same library we already depend on.

Key file shape: 32 bytes of CSPRNG output, urlsafe-base64
encoded into the format Fernet ingests directly. Persisted at
``<config_dir>/.device-builder-offload-key.bin`` with 0o600
perms. Same lifecycle policy as the cert key from phase 3a:
lazy-create on first paired-remote write; deletion is
unrecoverable (every stored sealed bearer becomes
unrecoverable too); rotation is out of scope until needed.

Encrypt at write time (``confirm_pair``); decrypt at read time
(future peer-link). The cleartext lives in process memory only
for the duration of a single outbound request; never written
back to disk in the clear.
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .atomic_io import atomic_write

_LOGGER = logging.getLogger(__name__)

_KEY_FILENAME = ".device-builder-offload-key.bin"
_KEY_MODE = 0o600

# Serialises lazy-creation of the keyfile so two pair attempts
# arriving milliseconds apart don't both write a fresh key (the
# loser's write would silently invalidate the winner's bearers).
_KEY_LOCK = threading.Lock()


class TokenSealError(Exception):
    """Sealing or unsealing failed (corrupted ciphertext, key mismatch, …)."""


def seal_bearer(config_dir: Path, cleartext: str) -> str:
    """
    Encrypt *cleartext* with the offloader's seal key.

    Returns Fernet ciphertext as a urlsafe-base64 string suitable
    for storing in the JSON metadata sidecar. Ciphertext is
    tamper-evident: any byte modification invalidates the HMAC
    and ``unseal_bearer`` raises :exc:`TokenSealError`.

    Lazy-creates the keyfile on first call. Subsequent calls
    reuse the cached :class:`Fernet` instance — keyfile reads
    don't touch disk on the hot path past startup.
    """
    fernet = _get_or_create_fernet(config_dir)
    return fernet.encrypt(cleartext.encode("utf-8")).decode("ascii")


def unseal_bearer(config_dir: Path, sealed: str) -> str:
    """
    Decrypt *sealed* and return the original cleartext bearer.

    Raises :exc:`TokenSealError` if the ciphertext is corrupted,
    the key has changed under us, or the input isn't valid
    Fernet wire format. Caller is expected to surface that as a
    typed error (peer-link would refuse the request rather than
    presenting garbage to the receiver).
    """
    fernet = _get_or_create_fernet(config_dir)
    try:
        return fernet.decrypt(sealed.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise TokenSealError("could not unseal stored bearer") from exc


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Cache one Fernet per config_dir. Tests use a fresh tmp_path
# per case; production has exactly one config_dir for the
# process lifetime, so this never grows unboundedly.
_FERNET_CACHE: dict[Path, Fernet] = {}


def _get_or_create_fernet(config_dir: Path) -> Fernet:
    """Return a :class:`Fernet` over the cached / persisted key."""
    cached = _FERNET_CACHE.get(config_dir)
    if cached is not None:
        return cached

    with _KEY_LOCK:
        # Re-check inside the lock — another caller may have
        # already populated the cache while we were waiting.
        cached = _FERNET_CACHE.get(config_dir)
        if cached is not None:
            return cached

        key_path = config_dir / _KEY_FILENAME
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            # Generate via Fernet's helper so the format is
            # exactly what Fernet expects; no manual urlsafe-b64.
            key = Fernet.generate_key()
            atomic_write(key_path, key, mode=_KEY_MODE)
            _LOGGER.info("Generated new offloader token-seal key at %s", key_path)
            # Belt-and-braces against unlikely truncation: the
            # key file MUST decode as a valid Fernet key.
            assert len(key) == 44, "Fernet.generate_key returned unexpected length"

        fernet = Fernet(key)
        _FERNET_CACHE[config_dir] = fernet
        return fernet


def _csprng_bytes(n: int) -> bytes:
    """
    Return *n* CSPRNG bytes; thin wrapper around :mod:`secrets`.

    Kept here so the test harness can monkeypatch a single seam
    when validating key-file content shape; production paths use
    Fernet's own ``generate_key`` which calls ``os.urandom``
    under the hood.
    """
    return secrets.token_bytes(n)
