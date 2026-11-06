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

from dkapp.research.forward import ForwardLedger  # noqa: E402
from dkapp.storage.db import Database  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("--plan", default="plan")
a = ap.parse_args()
mode = "live" if "live" in Path(a.db).name else "paper"
s = ForwardLedger(Database(a.db, mode)).summary(a.plan)
print(f"{s['observations']} observations {s['from']} → {s['to']} · chain intact: {s['chainIntact']}")
for g in s["groups"]:
    rng = "" if not g["ci90"] else f" (90%: {g['ci90'][0]:+.3f} … {g['ci90'][1]:+.3f})"
    net = "—" if g["netPct"] is None else f"{g['netPct']:+.3f}%"
    print(f"  {g['hypothesis']:38s} {g['decision']:10s} scored {g['withOutcome']:4d}/{g['observations']:4d}  net {net}{rng}"
          f"  pred-corr {g['predictionCorr']}")
if "--json" in sys.argv:
    print(json.dumps(s, indent=1))
