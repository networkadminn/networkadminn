"""Outbound email via company SMTP (aaPanel mail on same server)."""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


@dataclass
class SmtpConfig:
    host: str = "127.0.0.1"
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True  # STARTTLS on 587
    use_ssl: bool = False  # SMTPS on 465
    from_email: str = "no-reply@euclideesolutions.com"
    from_name: str = "esstracker"


def load_smtp_config(data_dir: str | None = None) -> SmtpConfig:
    """Load SMTP from env, then optional ``data_dir/smtp.toml``."""
    cfg = SmtpConfig(
        host=os.environ.get("ESSTRACKER_SMTP_HOST", "127.0.0.1"),
        port=int(os.environ.get("ESSTRACKER_SMTP_PORT", "587")),
        username=os.environ.get("ESSTRACKER_SMTP_USER", "no-reply@euclideesolutions.com"),
        password=os.environ.get("ESSTRACKER_SMTP_PASSWORD", ""),
        from_email=os.environ.get(
            "ESSTRACKER_SMTP_FROM", "no-reply@euclideesolutions.com"
        ),
        from_name=os.environ.get("ESSTRACKER_SMTP_FROM_NAME", "esstracker"),
    )
    mode = (os.environ.get("ESSTRACKER_SMTP_MODE") or "starttls").lower()
    if mode in ("ssl", "smtps", "465"):
        cfg.use_ssl = True
        cfg.use_tls = False
        if "ESSTRACKER_SMTP_PORT" not in os.environ:
            cfg.port = 465
    elif mode in ("plain", "none"):
        cfg.use_tls = False
        cfg.use_ssl = False

    toml_path = None
    if data_dir:
        toml_path = Path(data_dir) / "smtp.toml"
    elif os.environ.get("ESSTRACKER_SMTP_TOML"):
        toml_path = Path(os.environ["ESSTRACKER_SMTP_TOML"])
    if toml_path and toml_path.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover
            tomllib = None  # type: ignore
        if tomllib:
            with open(toml_path, "rb") as fh:
                data = tomllib.load(fh)
            smtp = data.get("smtp", data)
            for key in (
                "host",
                "port",
                "username",
                "password",
                "from_email",
                "from_name",
            ):
                if key in smtp and smtp[key] not in (None, ""):
                    setattr(cfg, key, type(getattr(cfg, key))(smtp[key]))
            if smtp.get("mode"):
                mode = str(smtp["mode"]).lower()
                if mode in ("ssl", "smtps", "465"):
                    cfg.use_ssl, cfg.use_tls = True, False
                elif mode in ("starttls", "tls", "587"):
                    cfg.use_ssl, cfg.use_tls = False, True

    # Same-server mail: prefer localhost if host is the public mail name
    if cfg.host in ("mail.euclideesolutions.com", "smtp.euclideesolutions.com"):
        # keep public host if password auth requires it; localhost also works on this box
        pass
    return cfg


def send_email(
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    data_dir: str | None = None,
) -> tuple[bool, str]:
    """Send one email. Returns (ok, error_message)."""
    cfg = load_smtp_config(data_dir)
    if not cfg.password:
        return False, "SMTP password not configured"
    if not to or "@" not in to:
        return False, "invalid recipient"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{cfg.from_name} <{cfg.from_email}>"
    msg["To"] = to
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=20, context=context) as smtp:
                smtp.login(cfg.username, cfg.password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=20) as smtp:
                smtp.ehlo()
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(cfg.username, cfg.password)
                smtp.send_message(msg)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surface to caller
        return False, str(exc)


__all__ = ["SmtpConfig", "load_smtp_config", "send_email"]
