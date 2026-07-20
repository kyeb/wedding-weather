#!/usr/bin/env python3
"""Recompute/verify the SMOKE constant from Cranbrook A hourly observations.

Usage:
  python3 scripts/recompute_smoke.py --fetch   # download the hourly corpus first (~480 files, 61 MB)
  python3 scripts/recompute_smoke.py           # check mode: diff recomputed old/new % vs SMOKE constant

Data: ECCC hourly observations at Cranbrook A — station 1174 (1968–2012) then
50818 (2012–present), Apr–Nov months. The 61 MB corpus is deliberately NOT
committed; --fetch rebuilds it in data/eccc-cranbrook-hourly/.

Definitions (match the page): a "smoky day" has >=1 hourly Weather observation
containing "Smoke". oldPct pools 1968–2014, newPct 2015–2025, over the same
Wed–Tue weekend windows as the temperature stats.

Audit findings (2026-07-19, independent recomputation):
- Reproduces the committed SMOKE old/new values on 25/27 weekends exactly
  (two windows differ by <0.5 pt at era edges).
- The ~10x old->new step change is REAL, not instrumental:
  (a) obs density is flat across the 2012 station handoff (23.6 -> 23.9 obs/day),
  (b) the new station's first years (2012–14) logged ~zero smoke — the step
      tracks the 2015+ fire seasons, not the hardware change,
  (c) requiring >=2 smoke-hours per day barely moves the numbers.
- hmsHeavyPct (NOAA HMS plumes) is still external; not recomputed here.
"""
import argparse, csv, datetime as dt, json, sys, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "eccc-cranbrook-hourly"
INDEX = ROOT / "index.html"
STATIONS = [(1174, 1968, 2012), (50818, 2012, 2026)]
MONTHS = range(4, 12)  # Apr-Nov: covers every weekend window the page scores


def fetch_missing() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sid, y0, y1 in STATIONS:
        for y in range(y0, y1 + 1):
            for m in MONTHS:
                f = DATA_DIR / f"cranbrook-{sid}-{y}-{m:02d}.csv"
                if f.exists() and f.stat().st_size > 0:
                    continue
                url = ("https://climate.weather.gc.ca/climate_data/bulk_data_e.html"
                       f"?format=csv&stationID={sid}&Year={y}&Month={m}&Day=1&timeframe=1")
                print(f"fetching {sid} {y}-{m:02d}...", file=sys.stderr)
                urllib.request.urlretrieve(url, f)


def load_days() -> dict:
    """(y,m,d) -> [smoke_hours, weather_obs_count], counting only non-empty Weather obs."""
    days = defaultdict(lambda: [0, 0])
    for f in DATA_DIR.glob("cranbrook-*.csv"):
        for row in csv.DictReader(open(f, encoding="utf-8-sig")):
            try:
                k = (int(row["Year"]), int(row["Month"]), int(row["Day"]))
            except (KeyError, ValueError):
                continue
            w = (row.get("Weather") or "").strip()
            if w:
                days[k][1] += 1
                if "Smoke" in w:
                    days[k][0] += 1
    return days


def era_pct(days: dict, fri: dt.date, years, min_hours: int = 1):
    pool = []
    for y in years:
        for wd in [fri + dt.timedelta(days=k) for k in range(-2, 5)]:
            try:
                d = wd.replace(year=y + (wd.year - 2027))
            except ValueError:
                continue
            rec = days.get((d.year, d.month, d.day))
            if rec and rec[1] > 0:
                pool.append(rec)
    if not pool:
        return None
    return round(100 * sum(1 for s, _ in pool if s >= min_hours) / len(pool), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    args = ap.parse_args()
    if args.fetch:
        fetch_missing()
    if not DATA_DIR.exists():
        sys.exit("no hourly corpus — run with --fetch first")

    days = load_days()
    src = INDEX.read_text()
    a = src.index("const SMOKE = [")
    e = src.index("];", a)
    smoke = json.loads(src[a + 14 : e + 1])

    diffs = 0
    for s in smoke:
        if s["oldPct"] is None:
            continue
        fri = dt.date.fromisoformat(s["start"])
        old = era_pct(days, fri, range(1968, 2015))
        new = era_pct(days, fri, range(2015, 2026))
        for key, mine in (("oldPct", old), ("newPct", new)):
            if abs(mine - s[key]) > 0.5:
                print(f"{s['start']} {key}: page {s[key]} vs recomputed {mine}")
                diffs += 1
    print(f"check complete: {diffs} values differ by >0.5 pt")


if __name__ == "__main__":
    main()
