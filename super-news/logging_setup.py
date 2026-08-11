"""Logging setup with defense-in-depth secret redaction.

Primary defense is discipline: code must never pass a secret value into a log
call. `register_secret()` is the backstop — any module that reads a secret
(client_secret, access_token, refresh_token, authorization code, ...) should
register it once, and any log line that happens to contain that exact string
gets it replaced before it reaches a handler.
"""

import logging
import logging.handlers

from config import LOG_DIR, ensure_runtime_dirs

_registered_secrets = set()

_REDACTED = "***REDACTED***"


def register_secret(value):
    """Record a live secret value so it gets redacted if it ever reaches a log call."""
    if value:
        _registered_secrets.add(value)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for secret in _registered_secrets:
            if secret and secret in redacted:
                redacted = redacted.replace(secret, _REDACTED)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level=logging.INFO):
    ensure_runtime_dirs()
    root = logging.getLogger()
    if root.handlers:
        return root

    root.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redaction_filter = SecretRedactionFilter()

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "super_news.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction_filter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redaction_filter)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    return root
