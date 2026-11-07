"""Rotating file logs. Secrets are scrubbed before anything is written."""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from . import paths

_SECRET_RE = re.compile(r"(?i)(secret|api[_-]?key|key[_-]?id|token|password)(['\"]?\s*[:=]\s*['\"]?)([^\s'\",}]+)")


class _Scrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        cleaned = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***", msg)
        if cleaned != msg:
            record.msg, record.args = cleaned, ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_dk_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(paths.log_dir() / "stockpilot.log", maxBytes=5_000_000, backupCount=5,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(_Scrubber())
    root.addHandler(fh)
    if sys.stderr is not None and getattr(sys, "frozen", False) is False:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.addFilter(_Scrubber())
        root.addHandler(sh)
    for noisy in ("urllib3", "httpx", "httpcore", "websockets", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._dk_configured = True  # type: ignore[attr-defined]
