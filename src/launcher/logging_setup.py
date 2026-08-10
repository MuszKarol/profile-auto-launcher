"""Rotating file logging.

`palaunchw` runs without a console, so stderr goes nowhere — the log file is
the only way to find out why a step failed. Every profile run writes there;
the console handler is added only when a terminal is actually attached.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from launcher.config import config_dir

LOGGER_NAME = "palaunch"
_configured = False


def log_dir() -> Path:
    return config_dir() / "logs"


def log_path() -> Path:
    return log_dir() / "palaunch.log"


def get_logger(name: str = "") -> logging.Logger:
    base = logging.getLogger(LOGGER_NAME)
    return base.getChild(name) if name else base


def setup(level: str | None = None, console: bool = False) -> logging.Logger:
    """Attach the rotating file handler once per process."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    from launcher import settings

    conf = settings.load()
    resolved = (level or conf.log_level or "INFO").upper()
    logger.setLevel(getattr(logging, resolved, logging.INFO))
    logger.propagate = False

    if os.environ.get("PAL_LOG_FILE") != "0":
        try:
            log_dir().mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path(),
                maxBytes=max(10_000, conf.log_max_bytes),
                backupCount=max(0, conf.log_backups),
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(handler)
        except OSError:
            pass  # logging must never break a run

    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        logger.addHandler(stream)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    _configured = True
    return logger


def tail(lines: int = 50) -> list[str]:
    """Last `lines` log lines, oldest first. Empty when nothing was logged."""
    path = log_path()
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:] if lines > 0 else content
