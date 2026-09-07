import base64
import hashlib

import bcrypt

_BCRYPT_SHA256_PREFIX = "{BCRYPT-SHA256}"


def _password_digest(password: str) -> bytes:
    """Keep bcrypt input fixed-size and avoid its 72-byte password limit."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_password_digest(password), bcrypt.gensalt()).decode("utf-8")
    return _BCRYPT_SHA256_PREFIX + hashed


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if password_hash.startswith(_BCRYPT_SHA256_PREFIX):
            encoded_hash = password_hash.removeprefix(_BCRYPT_SHA256_PREFIX).encode("utf-8")
            return bcrypt.checkpw(_password_digest(password), encoded_hash)

        # Backward compatibility for databases created by the stage-0 implementation.
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
