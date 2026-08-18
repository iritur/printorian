"""Encrypting secrets at rest.

ADR-0014: a printer's LAN access code lets anyone on the network read its state,
upload files to it and start prints. Stored in plain text, a routine database dump
copied to a laptop carries live credentials for the whole farm.

So the ciphertext lives in the database and the key lives outside it, in
``PRINTORIAN_SECRET_KEY``. A stolen dump is then not enough.

This is deliberately narrow: encrypt, decrypt, and a way to tell whether a value is
set without decrypting it. There is no "get me the plaintext for display" helper,
because there is no legitimate screen that shows an access code back to a user.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from printorian.core.errors import ConfigurationError, ValidationError

#: Marks values this module produced, so a plaintext column left over from an
#: earlier migration is detectable rather than silently fed to the decryptor.
PREFIX = "enc:v1:"


class SecretBox:
    """Symmetric encryption for values that must survive a database dump."""

    def __init__(self, key_material: str) -> None:
        if not key_material or len(key_material) < _MIN_KEY_LENGTH:
            raise ConfigurationError(
                "error.secrets.weak_key",
                hint=f"PRINTORIAN_SECRET_KEY must be at least {_MIN_KEY_LENGTH} characters.",
            )
        # Fernet needs 32 url-safe base64 bytes; the configured secret is arbitrary
        # text, so it is hashed to that shape rather than requiring operators to
        # generate a key in a specific format.
        digest = hashlib.sha256(key_material.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise ValidationError("error.secrets.empty")
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{PREFIX}{token}"

    def decrypt(self, stored: str) -> str:
        """Recover the plaintext, or fail loudly.

        A value that is not recognisably ours, or that will not decrypt with the
        current key, raises rather than being passed along. Handing a printer a
        corrupted access code would surface later as a confusing auth failure.
        """
        if not is_encrypted(stored):
            raise ValidationError("error.secrets.not_encrypted")
        try:
            return self._fernet.decrypt(stored.removeprefix(PREFIX).encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValidationError("error.secrets.undecryptable") from exc


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def is_set(value: str | None) -> bool:
    """Whether a secret exists, answerable without the key.

    This is what an API returns instead of the secret: "set" or "not set".
    """
    return bool(value)


_MIN_KEY_LENGTH = 16
