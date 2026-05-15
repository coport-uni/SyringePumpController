"""Single-package logger. The library never registers handlers; callers do."""

from __future__ import annotations

import logging

logger = logging.getLogger("sy01b")


def hex_preview(data: bytes, limit: int = 64) -> str:
    if len(data) <= limit:
        return data.hex()
    return data[:limit].hex() + f"... ({len(data)} bytes total)"
