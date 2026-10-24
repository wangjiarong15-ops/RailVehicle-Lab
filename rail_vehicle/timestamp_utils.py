"""Consistent parsing helpers for ISO 8601 timestamps."""

from datetime import datetime


def parse_iso_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp, including UTC ``Z`` on Python 3.10.

    ``datetime.fromisoformat`` added direct ``Z`` support in Python 3.11. CSV
    input already normalizes this suffix, but internal analysis helpers also
    accept timestamps directly, so normalize it here for consistent behavior.
    """
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    return datetime.fromisoformat(normalized)
