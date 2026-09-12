"""argon2 password hashing.

Uses `argon2-cffi` defaults. Verification is constant-time via the
library. Rehashing on parameter drift is handled by the hasher; we
report `needs_rehash()` to the caller so it can migrate at next login.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(hash_str: str, pw: str) -> bool:
    try:
        return _hasher.verify(hash_str, pw)
    except VerifyMismatchError:
        return False


def needs_rehash(hash_str: str) -> bool:
    return _hasher.check_needs_rehash(hash_str)
