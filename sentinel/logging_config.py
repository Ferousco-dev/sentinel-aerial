"""Centralised logging setup.

Kept in its own module so every entry point configures logging identically and
library code never calls ``print``. Handlers are installed once and idempotently.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def configure(level: str = "INFO") -> None:
    """Install a single stderr handler on the root logger.

    Safe to call multiple times; only the first call installs handlers, later
    calls just adjust the level.
    """
    global _CONFIGURED
    root = logging.getLogger()
    resolved = getattr(logging, level.upper(), logging.INFO)

    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        root.addHandler(handler)
        _CONFIGURED = True

    root.setLevel(resolved)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger (e.g. ``sentinel.video``)."""
    return logging.getLogger(name)
