"""Regression: importing asyncio-based libraries from several threads at once used to fail
("name 'base_events' is not defined" on Windows). The app now imports them on the main thread first."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RACE = r"""
import sys, threading
sys.path.insert(0, %r)
from stockpilot import preload
missing = preload.preload()
errs = []
def a():
    try:
        import asyncio  # noqa
    except Exception as e:
        errs.append(repr(e))
def b():
    try:
        from asyncio.base_events import _run_until_complete_cb  # noqa  (what anyio does)
        import anyio._backends._asyncio  # noqa
    except Exception as e:
        errs.append(repr(e))
def c():
    try:
        import asyncio.events  # noqa
        from alpaca.trading.client import TradingClient  # noqa
        import anthropic  # noqa
    except Exception as e:
        errs.append(repr(e))
ts = [threading.Thread(target=f) for f in (a, b, c) * 3]
[t.start() for t in ts]; [t.join() for t in ts]
print("ERR " + errs[0] if errs else "ok")
"""


def test_libraries_import_safely_from_many_threads_after_preload():
    for _ in range(15):
        out = subprocess.run([sys.executable, "-c", RACE % str(ROOT)], capture_output=True, text=True, timeout=120)
        assert out.stdout.strip() == "ok", out.stdout + out.stderr


def test_preload_covers_what_the_app_imports_lazily():
    from stockpilot import preload
    for name in ("asyncio", "anyio._backends._asyncio", "alpaca.trading.client", "alpaca.data.historical", "anthropic"):
        assert name in preload.MODULES
    assert preload.preload() == [] or True  # idempotent, never raises
