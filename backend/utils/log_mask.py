"""
Log masking utilities for PII protection (T128: security hardening).

Provides helpers to mask emails, truncate user-generated content, and
sanitize field values before they are emitted in structured log events.
"""


def mask_email(email: str) -> str:
    """Mask an email address: keep domain, hide local part.

    ``"alice@example.com"`` → ``"a***@example.com"``
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.rsplit("@", 1)
    if len(local) <= 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_username(username: str) -> str:
    """Partially mask a username.

    ``"zhangsan"`` → ``"z***n"`` (first + last visible, rest masked)
    """
    if not username:
        return "***"
    if len(username) <= 2:
        return username[0] + "***"
    return username[0] + "***" + username[-1]


def safe_preview(text: str, max_chars: int = 30) -> str:
    """Truncate user-generated text to a safe preview length for logs.

    Returns just the character count if the text is non-empty, to avoid
    logging actual message content.
    """
    if not text:
        return "<empty>"
    return f"<{len(text)} chars>"


def sanitize_extra_fields(fields: dict) -> dict:
    """Return a copy with values replaced by their type indicator.

    Used to prevent ``**extra_fields`` from leaking sensitive data into
    structured log events.
    """
    if not fields:
        return {}
    return {k: f"<{type(v).__name__}>" for k, v in fields.items()}
