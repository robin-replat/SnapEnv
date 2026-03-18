"""Tests for webhook signature verification.

The HMAC verification is critical for security — these tests ensure
it correctly accepts valid signatures and rejects invalid ones.
"""

import hashlib
import hmac

from src.api.routes.webhooks import verify_github_signature


class TestVerifyGithubSignature:
    """Tests for the HMAC-SHA256 signature verification."""

    def test_valid_signature_accepted(self) -> None:
        """A correctly signed payload should return True."""
        secret = "my-webhook-secret"  # noqa: S105
        payload = b'{"action": "opened"}'
        signature = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert verify_github_signature(payload, signature, secret) is True

    def test_invalid_signature_rejected(self) -> None:
        """A payload with a wrong signature should return False."""
        secret = "my-webhook-secret"  # noqa: S105
        payload = b'{"action": "opened"}'

        assert verify_github_signature(payload, "sha256=deadbeef", secret) is False

    def test_tampered_payload_rejected(self) -> None:
        """If the payload is modified after signing, verification fails."""
        secret = "my-webhook-secret"  # noqa: S105
        original_payload = b'{"action": "opened"}'
        signature = "sha256=" + hmac.new(secret.encode(), original_payload, hashlib.sha256).hexdigest()

        tampered_payload = b'{"action": "closed"}'
        assert verify_github_signature(tampered_payload, signature, secret) is False

    def test_empty_secret_skips_verification(self) -> None:
        """When no secret is configured, verification is skipped (dev mode)."""
        assert verify_github_signature(b"anything", "whatever", "") is True

    def test_wrong_secret_rejected(self) -> None:
        """Signing with a different secret should be rejected."""
        payload = b'{"action": "opened"}'
        signature = "sha256=" + hmac.new(b"secret-a", payload, hashlib.sha256).hexdigest()

        assert verify_github_signature(payload, signature, "secret-b") is False
