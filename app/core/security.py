"""Password hashing and web-session tokens.

Uses PBKDF2-HMAC-SHA256 from the standard library instead of argon2/bcrypt so the
image stays dependency-light and builds cleanly on both amd64 and arm64 with no
compiled crypto wheels. 200k iterations is comfortably above interactive-login
cost while staying fast enough for a self-hosted panel.
"""

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """Return an encoded hash: ``algo$iterations$salt$digest`` (all hex/base)."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification of a password against an encoded hash."""
    try:
        algo, iters, salt, digest = encoded.split("$")
        if algo != _ALGO:
            return False
        expected = bytes.fromhex(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(expected, actual)


def new_token(nbytes: int = 32) -> str:
    """Cryptographically strong URL-safe token for session cookies / secrets."""
    return secrets.token_urlsafe(nbytes)
