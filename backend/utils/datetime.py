"""
Datetime helpers.

A single place for the project's wall-clock timestamp idiom:
``datetime.now(timezone.utc)`` (Constitution VI — real audit timestamps, not
workflow monotonic time). Centralised here so every agent / service stamps
records the same way instead of re-declaring a local ``_now_iso``.

Related: structured logging lives in :mod:`backend.utils.logging`.
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """UTC now as an ISO-8601, timezone-aware string."""
    return datetime.now(UTC).isoformat()


def now_dt() -> datetime:
    """UTC now as a timezone-aware ``datetime`` (for DB column assignments)."""
    return datetime.now(UTC)
