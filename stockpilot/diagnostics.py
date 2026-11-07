"""One-click diagnostics bundle: everything needed to figure out what went wrong, nothing secret.

Contents of the .zip:
  summary.txt   app/OS versions, mode, health, status, alerts, settings (no keys), recent orders and events
  logs/         the app's rotating logs (secrets are scrubbed when written, and again here)
  extra/        the self-test report and the installer/uninstaller log, if present

API keys live in the operating system's credential store and are never read into the bundle —
only whether each one is saved. Databases are summarized, not copied.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import APP_ID, __version__, credentials, paths
from .broker.explain import explain_rejection
from .config import AppSettings
from .log import _SECRET_RE

# long random-looking tokens (API keys look like this) are masked even without a "key=" label
import re
_TOKEN_RE = re.compile(r"\b(?:PK|AK|CK|sk-ant-)[A-Za-z0-9_\-]{12,}\b|\b[A-Za-z0-9]{32,}\b")


def scrub(text: str) -> str:
    text = _SECRET_RE.sub(lambda m: m.group(1) + m.group(2) + "***", text)
    return _TOKEN_RE.sub("***", text)


def _db_summary(mode: str) -> dict:
    from .storage.db import Database
    f = paths.database_file(mode)
    if not f.exists():
        return {"exists": False}
    db = Database(f, mode)
    try:
        out = {"exists": True, "sizeKb": round(f.stat().st_size / 1024)}
        for table in ("trades", "orders", "signals", "ai_decisions", "shadow_trades", "decision_cycles", "events"):
            out[table] = int(db.scalar(f"SELECT COUNT(*) FROM {table}", (), 0))
        out["openTrades"] = [{k: t[k] for k in ("id", "symbol", "status", "entry_qty", "exit_qty", "stop_price",
                                                  "software_stop", "session_date")} for t in db.open_trades()]
        out["recentOrders"] = [
            {"time": o["created_at"], "symbol": o["symbol"], "side": o["side"], "type": o["type"],
             "purpose": o["purpose"], "status": o["status"],
             "why": (explain_rejection(o["reject_reason"]) + " | " + scrub(o["reject_reason"])) if o["reject_reason"] else ""}
            for o in db.query("SELECT * FROM orders ORDER BY id DESC LIMIT 40")]
        out["recentEvents"] = [f'{e["time"]} [{e["level"]}/{e["category"]}] {e["title"]} — {scrub(e["message"] or "")}'
                               for e in db.query("SELECT * FROM events ORDER BY id DESC LIMIT 200")]
        return out
    finally:
        db.close()


def build(settings: AppSettings, mode: str, snapshot: Optional[dict] = None, dest_dir: Optional[Path] = None) -> Path:
    dest_dir = dest_dir or _default_dest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = dest_dir / f"{APP_ID}-diagnostics-{stamp}.zip"
    snap = dict(snapshot or {})
    for big in ("scanner", "chart"):
        snap.pop(big, None)
    store_ok, store = credentials.secure_backend_available()
    summary = {
        "app": {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0],
                "os": platform.platform(), "machine": platform.machine()},
        "mode": mode,
        "credentials": {"secureStore": store_ok, "store": store,
                        "paperKeysSaved": credentials.load_alpaca_keys("paper") is not None,
                        "liveKeysSaved": credentials.load_alpaca_keys("live") is not None,
                        "anthropicKeySaved": bool(credentials.load_anthropic_key())},
        "settings": json.loads(settings.model_dump_json()),
        "status": snap.get("status"), "health": snap.get("health"), "alerts": snap.get("alerts"),
        "session": snap.get("session"), "account": snap.get("account"), "regime": snap.get("regime"),
        "lastDecision": snap.get("lastDecision"),
        "database": _db_summary(mode),
    }
    text = scrub(json.dumps(summary, indent=2, default=str))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.txt", text)
        for f in sorted(paths.log_dir().glob("*.log*")):
            try:
                z.writestr(f"logs/{f.name}", scrub(f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
        for extra in (paths.log_dir() / "self-test.txt", Path(tempfile.gettempdir()) / f"{APP_ID}-uninstall.log"):
            if extra.exists():
                z.writestr(f"extra/{extra.name}", scrub(extra.read_text(encoding="utf-8", errors="replace")))
    return out


def _default_dest() -> Path:
    home = Path.home()
    for d in (home / "Desktop", home / "Downloads", home):
        if d.is_dir():
            return d
    return paths.data_dir()
