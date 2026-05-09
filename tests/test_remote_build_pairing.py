"""
Tests for the offloader-side pairing helpers (phase 4a).

The TLS-actual round-trip is exercised end-to-end in
``test_remote_build_pair_e2e.py`` against a real receiver
instance. This file covers the pure pieces:

- ``_spki_fingerprint_from_der`` matches the same fingerprint
  ``helpers.dashboard_identity._spki_fingerprint`` produces (the
  controller compares against pins from that helper's output, so
  the two MUST agree byte-for-byte).
- ``_format_host`` brackets bare IPv6 literals.
- :exc:`PinMismatchError` carries enough context for a typed
  controller error message.
"""

from __future__ import annotations

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from esphome_device_builder.helpers.dashboard_identity import _generate_cert_pair
from esphome_device_builder.helpers.remote_build_pairing import (
    PinMismatchError,
    _format_host,
    _spki_fingerprint_from_der,
)


def test_spki_fingerprint_from_der_is_lowercase_hex() -> None:
    cert_pem, _ = _generate_cert_pair()
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert_der = cert.public_bytes(encoding=serialization.Encoding.DER)
    pin = _spki_fingerprint_from_der(cert_der)
    assert len(pin) == 64
    assert pin == pin.lower()
    int(pin, 16)  # parses as hex


def test_spki_fingerprint_from_der_matches_dashboard_identity() -> None:
    """
    Same input bytes → same output fingerprint, across the two helpers.

    The controller's ``confirm_pair`` compares an offloader's
    observed pin against a pin computed by ``dashboard_identity._spki_fingerprint``.
    A drift between the two would silently break every pair attempt.
    """
    cert_pem, _ = _generate_cert_pair()
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert_der = cert.public_bytes(encoding=serialization.Encoding.DER)

    pairing_pin = _spki_fingerprint_from_der(cert_der)

    # Recompute the same fingerprint via the SPKI path the
    # dashboard_identity helper uses.
    spki = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(spki)
    expected = digest.finalize().hex()
    assert pairing_pin == expected


def test_format_host_brackets_ipv6() -> None:
    assert _format_host("fe80::1") == "[fe80::1]"
    assert _format_host("::1") == "[::1]"


def test_format_host_passes_already_bracketed_through() -> None:
    assert _format_host("[fe80::1]") == "[fe80::1]"


def test_format_host_leaves_ipv4_and_hostnames_alone() -> None:
    assert _format_host("192.168.1.10") == "192.168.1.10"
    assert _format_host("desktop.local") == "desktop.local"


def test_pin_mismatch_error_carries_context() -> None:
    err = PinMismatchError(expected="a" * 64, observed="b" * 64)
    assert err.expected == "a" * 64
    assert err.observed == "b" * 64
    # The string repr includes both so logs / typed messages can
    # surface the divergence to the user.
    assert "a" * 64 in str(err)
    assert "b" * 64 in str(err)
