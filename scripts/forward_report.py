"""Summarize the forward research ledger of a paper (or live) database.

    python scripts/forward_report.py PATH_TO/trading_paper.db [--plan plan|h30|h60|h120|close]

Prints, per setup type and decision, how many observations were scored and the hypothetical net result per
trade after costs with a 90% day-block range, and checks that no recorded prediction was edited.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone  # noqa: E402

from dkapp.clock import MarketCalendar  # noqa: E402
from dkapp.research.daily_shadow import HYPOTHESES as DAILY, DailyShadow  # noqa: E402
from dkapp.research.forward import ForwardLedger  # noqa: E402
from dkapp.storage.db import Database  # noqa: E402

# what the research measured, so forward results can be read as "holding up" or "fading" (per holding, net)
EXPECTED = {
    "P3-H1 sector relative strength (shadow)": "history: +0.13%/month over equal weight (1926-2005); +0.17% on ETFs 2023-26 (n.s.)",
    "P3-H2 turn of the month (shadow)": "history: +0.52%/window 1926-2005, +0.04%/window 2006-2020 (gone)",
    "P3 benchmark: SPY buy-and-hold (shadow)": "the market itself",
}

ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("--plan", default="plan")
a = ap.parse_args()
mode = "live" if "live" in Path(a.db).name else "paper"
led = ForwardLedger(Database(a.db, mode))
s = led.summary(a.plan, exclude=DAILY)
print(f"{s['observations']} observations {s['from']} → {s['to']} · chain intact: {s['chainIntact']}")
for g in s["groups"]:
    rng = "" if not g["ci90"] else f" (90%: {g['ci90'][0]:+.3f} … {g['ci90'][1]:+.3f})"
    net = "—" if g["netPct"] is None else f"{g['netPct']:+.3f}%"
    print(f"  {g['hypothesis']:38s} {g['decision']:10s} scored {g['withOutcome']:4d}/{g['observations']:4d}  net {net}{rng}"
          f"  pred-corr {g['predictionCorr']}")
d = DailyShadow(led, MarketCalendar()).status(datetime.now(timezone.utc))
print("\nmulti-day rules (shadow only, never traded):")
for g in d["strategies"]:
    net = "—" if g["netPct"] is None else f"{g['netPct']:+.2f}%"
    vs = "—" if g["vsSpyPct"] is None else f"{g['vsSpyPct']:+.2f}%"
    print(f"  {g['hypothesis']:42s} recorded {g['recorded']:3d} missed {g['missed']:3d} scored {g['scored']:3d}  "
          f"per holding {net}  vs SPY {vs}  ({g['verdict']})")
    print(f"      {EXPECTED.get(g['hypothesis'], '')}")
for n in d["next"]:
    print(f"  next window: {n['hypothesis']}: {n['opens']} → {n['closes']}")
s["daily"] = d
if "--json" in sys.argv:
    print(json.dumps(s, indent=1, default=str))
