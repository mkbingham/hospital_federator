from __future__ import annotations

import logging
from typing import Optional


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure application-wide logging."""

    level_name = (level or "INFO").upper().strip()
    numeric = getattr(logging, level_name, logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=numeric,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
