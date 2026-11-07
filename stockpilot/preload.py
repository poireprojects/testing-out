"""Import the libraries that use asyncio once, on the main thread, before any worker thread starts.

Python's import of the ``asyncio`` package is not safe when two threads trigger it at the same moment:
the second thread sees a half-initialized package and fails with errors such as
"name 'base_events' is not defined" (seen on Windows) or "partially initialized module
'asyncio.events' ...". The app imports the Alpaca and AI client libraries lazily, from background
threads (the brokerage test button, AI health checks, the trading engine, backtests), so whichever
got there first could break the others. Importing them here first makes every later import a
plain lookup.
"""
from __future__ import annotations

import importlib
import logging
import sys

log = logging.getLogger(__name__)

MODULES = [
    "asyncio", "asyncio.base_events", "asyncio.events", "asyncio.tasks", "asyncio.streams",
    "concurrent.futures", "concurrent.futures.thread", "ssl", "socket", "selectors", "sqlite3",
    "sniffio", "anyio", "anyio._backends._asyncio", "h11", "httpcore", "httpx",
    "urllib3", "requests", "websockets", "msgpack", "pydantic", "numpy", "pandas", "keyring",
    "alpaca", "alpaca.common.rest", "alpaca.common.exceptions", "alpaca.trading.client", "alpaca.trading.requests",
    "alpaca.trading.enums", "alpaca.data.historical", "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.data.enums", "anthropic",
]
if sys.platform == "win32":
    MODULES[1:1] = ["asyncio.windows_events", "asyncio.proactor_events", "asyncio.windows_utils"]

_done = False


def preload() -> list[str]:
    """Import everything above (once). Returns the modules that couldn't be imported (optional
    extras that aren't installed are fine — they're simply skipped)."""
    global _done
    if _done:
        return []
    failed = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # an optional library missing must never stop the app
            failed.append(f"{name}: {exc}")
    _done = True
    if failed:
        log.info("preload skipped: %s", "; ".join(failed))
    return failed
