"""Phase 3 data: survivorship-free academic daily returns (Ken French data library). No credentials.

    python scripts/download_phase3.py OUT_DIR

Each file is parsed in memory and split by research/ledger.json before anything is written:
  OUT_DIR/dev/<name>.csv     development days (1926-07-01 .. 2005-12-30, and 2022-11-01 onward)
  OUT_DIR/holdA/<name>.csv   phase-3 holdout A (2006-01-03 .. 2020-07-24): written, never read by development code
Rows inside the vault (2020-07-27 .. 2022-10-31) are DROPPED in memory and never written.
Returns are in percent, as published (value-weighted sections only; -99.99 / -999 mean missing).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dkapp.research.ledger import EXAMINED, RESERVED, DataLedger  # noqa: E402

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {
    "ff3_daily": "F-F_Research_Data_Factors_daily_CSV.zip",     # Mkt-RF, SMB, HML, RF
    "mom_daily": "F-F_Momentum_Factor_daily_CSV.zip",           # UMD
    "ind12_daily": "12_Industry_Portfolios_daily_CSV.zip",       # value-weighted section
    "ind49_daily": "49_Industry_Portfolios_daily_CSV.zip",       # value-weighted section
}
VAULT = (date(2020, 7, 27), date(2022, 10, 31))
HOLD_A = (date(2006, 1, 3), date(2020, 7, 24))


def fetch(name: str) -> bytes:
    for attempt in range(4):
        try:
            req = urllib.request.Request(BASE + name, headers={"User-Agent": "dkapp-research/phase3"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as exc:  # network hiccup
            print("  retry", attempt + 1, str(exc)[:100], flush=True)
            time.sleep(2 * 2 ** attempt)
    raise RuntimeError(f"download failed: {name}")


def parse_first_section(text: str) -> tuple[list[str], list[tuple[date, list[float]]]]:
    """The first table in the file (for industry files: 'Average Value Weighted Returns -- Daily')."""
    header, rows, started = None, [], False
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if not started:
            if len(cells) > 1 and cells[0] == "" and all(c for c in cells[1:]):
                header, started = cells[1:], True
            continue
        if not cells[0].isdigit() or len(cells[0]) != 8:
            break                                   # end of the first section
        d = date(int(cells[0][:4]), int(cells[0][4:6]), int(cells[0][6:]))
        rows.append((d, [float(c) for c in cells[1:]]))
    if header is None:
        raise ValueError("no table found")
    return header, rows


def split_and_write(key: str, header: list[str], rows, out: Path, ledger: DataLedger) -> dict:
    parts = {"dev": [], "holdA": []}
    dropped = 0
    for d, vals in rows:
        if VAULT[0] <= d <= VAULT[1]:
            dropped += 1                            # never written, never looked at
            continue
        if HOLD_A[0] <= d <= HOLD_A[1]:
            assert ledger.status(d) == RESERVED, d
            parts["holdA"].append((d, vals))
        else:
            if ledger.status(d) != EXAMINED:
                raise SystemExit(f"{d} is not registered as development data in research/ledger.json")
            parts["dev"].append((d, vals))
    info = {"vaultRowsDropped": dropped}
    for part, rs in parts.items():
        (out / part).mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["date"] + header)
        for d, vals in rs:
            w.writerow([d.isoformat()] + [f"{v:.4f}" for v in vals])
        data = buf.getvalue().encode()
        (out / part / f"{key}.csv").write_bytes(data)
        info[part] = {"rows": len(rs), "first": str(rs[0][0]) if rs else None, "last": str(rs[-1][0]) if rs else None,
                      "sha256": hashlib.sha256(data).hexdigest()}
    return info


def main(out: Path) -> int:
    ledger = DataLedger.load()
    manifest = {"source": BASE, "downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": {}}
    for key, name in FILES.items():
        raw = fetch(name)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            text = z.read(z.namelist()[0]).decode("latin-1")
        header, rows = parse_first_section(text)
        manifest["files"][key] = {"file": name, "columns": header} | split_and_write(key, header, rows, out, ledger)
        print(key, json.dumps({k: v for k, v in manifest["files"][key].items() if k != "columns"}), flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
