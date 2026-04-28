"""
logger.py — Configurazione centralizzata del logging.
"""

import logging
import sys
from config.settings import LOG_LEVEL


def setup_logging() -> None:
    """Configura il logger root con formato leggibile e output su stdout."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s"
    datefmt = "%H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
