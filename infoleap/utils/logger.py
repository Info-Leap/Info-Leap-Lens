"""
OxData Logger
=============
Centralized logging for all OxData modules. Every module imports from here
so logs are consistently formatted and routed to both console and file.

Usage:
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Something happened")
    log.debug("Detail: %s", value)
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

# ── log directory ──────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── file per session (date-based) ─────────────────────────────────────────────
_LOG_FILE = LOG_DIR / f"oxdata_{datetime.now().strftime('%Y%m%d')}.log"

# ── formatter ──────────────────────────────────────────────────────────────────
_FMT = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-7s | %(name)-30s | %(message)s",
    datefmt="%H:%M:%S",
)

# ── handlers (created once, shared across all loggers) ─────────────────────────
_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_FMT)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_FMT)

# ── root config ────────────────────────────────────────────────────────────────
_root = logging.getLogger("oxdata")
_root.setLevel(logging.DEBUG)
if not _root.handlers:
    _root.addHandler(_file_handler)
    _root.addHandler(_console_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Get a child logger under the 'oxdata' namespace.

    Args:
        name: Usually __name__ of the calling module.
              e.g., "skills.capabilities.compare" → "oxdata.skills.capabilities.compare"

    Returns:
        A configured Logger instance.
    """
    # Strip common prefixes for cleaner log names
    clean = name.replace("skills.capabilities.", "cap.").replace("views.", "ui.")
    return _root.getChild(clean)


def log_separator(logger: logging.Logger, label: str = "") -> None:
    """Print a visual separator in the log for readability."""
    logger.info("─" * 60 + (" " + label if label else ""))


def log_token_usage(logger: logging.Logger, tokens_in: int, tokens_out: int,
                    model: str, purpose: str) -> None:
    """Log API token usage in a standardized format."""
    logger.info(
        "TOKENS | model=%s purpose=%s in=%d out=%d total=%d",
        model, purpose, tokens_in, tokens_out, tokens_in + tokens_out,
    )
