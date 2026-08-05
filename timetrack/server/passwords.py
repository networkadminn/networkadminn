"""Password strength policy for admin-provisioned accounts.

Enforced when creating users or setting passwords via the CLI. Kept out of
``User.set_password`` so that programmatic/test fixtures can still assign
arbitrary hashes, while human-facing provisioning gets a strong default policy.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

_SPECIALS = set(string.punctuation)

# A small blocklist of obviously weak passwords. Not exhaustive — it just
# catches the most embarrassing choices.
_COMMON = {
    "password", "password1", "passw0rd", "12345678", "123456789", "1234567890",
    "qwerty", "qwertyuiop", "letmein", "admin", "administrator", "welcome",
    "iloveyou", "changeme", "timetrack", "secret", "abc123", "111111",
}


@dataclass
class PasswordPolicy:
    min_length: int = 12
    require_lower: bool = True
    require_upper: bool = True
    require_digit: bool = True
    require_symbol: bool = True

    def problems(self, password: str, *, username: str | None = None) -> list[str]:
        """Return a list of human-readable policy violations (empty = OK)."""
        issues: list[str] = []
        pw = password or ""

        if len(pw) < self.min_length:
            issues.append(f"must be at least {self.min_length} characters long")
        if self.require_lower and not any(c.islower() for c in pw):
            issues.append("must contain a lowercase letter")
        if self.require_upper and not any(c.isupper() for c in pw):
            issues.append("must contain an uppercase letter")
        if self.require_digit and not any(c.isdigit() for c in pw):
            issues.append("must contain a digit")
        if self.require_symbol and not any(c in _SPECIALS for c in pw):
            issues.append("must contain a symbol")
        if pw.lower() in _COMMON:
            issues.append("is too common / easily guessed")
        if username and username.lower() in pw.lower() and len(username) >= 3:
            issues.append("must not contain the username")

        return issues

    def validate(self, password: str, *, username: str | None = None) -> None:
        """Raise ``ValueError`` describing any policy violations."""
        issues = self.problems(password, username=username)
        if issues:
            raise ValueError("Password " + "; ".join(issues) + ".")


DEFAULT_POLICY = PasswordPolicy()


def validate_password(
    password: str, *, username: str | None = None, policy: PasswordPolicy | None = None
) -> None:
    (policy or DEFAULT_POLICY).validate(password, username=username)


__all__ = ["PasswordPolicy", "DEFAULT_POLICY", "validate_password"]
