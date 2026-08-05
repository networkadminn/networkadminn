"""Authentication hardening helpers for the multi-user server.

Covers the account-level controls that a reseller/shared-hosting tier
can't apply for you at the server level, but that *are* fully within an
application's own power:

- a minimum password-strength policy (enforced whenever a password is set)
- optional TOTP-based two-factor authentication (2FA/MFA) per user
- IP allow-listing for the admin dashboard
"""

from __future__ import annotations

import ipaddress
import re

PASSWORD_MIN_LENGTH = 12

# Each entry maps a human-readable label to the pattern that satisfies it.
_CHAR_CLASSES: dict[str, re.Pattern[str]] = {
    "an uppercase letter": re.compile(r"[A-Z]"),
    "a lowercase letter": re.compile(r"[a-z]"),
    "a digit": re.compile(r"\d"),
    "a special character": re.compile(r"[^A-Za-z0-9]"),
}
# Require at least this many of the four character classes above.
_MIN_CHAR_CLASSES = 3

_COMMON_PASSWORDS = {
    "password",
    "password123",
    "letmein123",
    "qwertyuiop",
    "admin12345",
    "changeme123",
    "welcome123",
}


class WeakPasswordError(ValueError):
    """Raised when a password does not meet the minimum strength policy."""


def password_policy_violations(
    password: str, *, min_length: int = PASSWORD_MIN_LENGTH
) -> list[str]:
    """Return a list of human-readable policy violations (empty == OK)."""
    issues: list[str] = []
    if len(password) < min_length:
        issues.append(f"must be at least {min_length} characters long")

    satisfied = sum(1 for pattern in _CHAR_CLASSES.values() if pattern.search(password))
    if satisfied < _MIN_CHAR_CLASSES:
        issues.append(
            "must include at least three of the following: "
            + ", ".join(_CHAR_CLASSES)
        )

    if password.lower() in _COMMON_PASSWORDS:
        issues.append("must not be a commonly used password")

    return issues


def validate_password_strength(
    password: str, *, min_length: int = PASSWORD_MIN_LENGTH
) -> None:
    """Raise :class:`WeakPasswordError` if ``password`` is too weak."""
    issues = password_policy_violations(password, min_length=min_length)
    if issues:
        raise WeakPasswordError("Weak password: " + "; ".join(issues))


# --- Two-factor authentication (TOTP) -------------------------------------


def generate_mfa_secret() -> str:
    """Generate a new base32 TOTP secret (requires the optional ``pyotp`` dep)."""
    import pyotp

    return pyotp.random_base32()


def mfa_provisioning_uri(secret: str, *, username: str, issuer: str = "TimeTrack") -> str:
    """Return a ``otpauth://`` URI suitable for a QR code / authenticator app."""
    import pyotp

    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_mfa_code(secret: str, code: str) -> bool:
    """Check a 6-digit TOTP code against ``secret`` (1-step clock skew allowed)."""
    if not secret or not code:
        return False
    import pyotp

    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


# --- Admin IP allow-listing ------------------------------------------------


def client_ip_allowed(remote_addr: str | None, allowlist: list[str]) -> bool:
    """Return True if ``remote_addr`` matches an entry in ``allowlist``.

    ``allowlist`` entries may be single IPs or CIDR ranges (IPv4 or IPv6).
    An empty allowlist means "no restriction" (always returns True), which
    mirrors WHM's Host Access Control default of allowing everyone unless
    rules are configured.
    """
    if not allowlist:
        return True
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False

    for entry in allowlist:
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


__all__ = [
    "PASSWORD_MIN_LENGTH",
    "WeakPasswordError",
    "password_policy_violations",
    "validate_password_strength",
    "generate_mfa_secret",
    "mfa_provisioning_uri",
    "verify_mfa_code",
    "client_ip_allowed",
]
