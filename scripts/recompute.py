#!/usr/bin/env python3
"""Recompute the per-weekend weather stats in index.html from raw ECCC dailies.

Usage:
  python3 scripts/recompute.py            # check mode: diff recomputed vs committed DATA
  python3 scripts/recompute.py --write    # patch index.html's DATA constant in place
  python3 scripts/recompute.py --fetch    # (re)download any missing yearly CSVs first

Data: data/eccc-fernie-daily/fernie-1180-<year>.csv — Environment and Climate
Change Canada daily records, FERNIE station (climate ID 1152850, internal
stationID 1180), via the bulk_data_e.html CSV endpoint. Open Government License.

Method (mirrors the page's methodology note): each Fri–Sun weekend of 2027 is
scored over a 7-day Wednesday–Tuesday window centered on it, pooling that
window's calendar days across all years. "Rainy day" = Total Rain > 0 mm.
"Hot day" = Max Temp > 30 °C (86 °F). DATA temps are stored in °C (the page
converts to °F at render time).

NOT covered here: the SMOKE constant (Cranbrook A hourly observations + NOAA
HMS plumes) — that pipeline was never committed; treat SMOKE as frozen unless
rebuilt from scratch.
"""
import argparse, csv, datetime as dt, json, re, statistics as st, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "eccc-fernie-daily"
INDEX = ROOT / "index.html"
STATION_ID = 1180  # ECCC internal id for FERNIE (climate ID 1152850)
FIRST_YEAR = 1913


def fetch_missing(last_year: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for y in range(FIRST_YEAR, last_year + 1):
        f = DATA_DIR / f"fernie-{STATION_ID}-{y}.csv"
        if f.exists():
            continue
        url = ("https://climate.weather.gc.ca/climate_data/bulk_data_e.html"
               f"?format=csv&stationID={STATION_ID}&Year={y}&Month=1&Day=1&timeframe=2")
        print(f"fetching {y}...", file=sys.stderr)
        urllib.request.urlretrieve(url, f)


def load_records() -> dict:
    recs = {}
    for f in sorted(DATA_DIR.glob(f"fernie-{STATION_ID}-*.csv")):
        for row in csv.DictReader(open(f, encoding="utf-8-sig")):
            try:
                key = (int(row["Year"]), int(row["Month"]), int(row["Day"]))
            except (KeyError, ValueError):
                continue

            def num(col):
                try:
                    return float(row.get(col, "").strip())
                except ValueError:
                    return None

            recs[key] = (num("Max Temp (°C)"), num("Min Temp (°C)"),
                         num("Total Rain (mm)"), num("Total Snow (cm)"))
    return recs


def weekend_stats(recs: dict, fri: dt.date, last_year: int) -> dict:
    window = [fri + dt.timedelta(days=k) for k in range(-2, 5)]  # Wed..Tue
    his, los, rains = [], [], []
    n = rainy = snowy = hot = 0
    for y in range(FIRST_YEAR, last_year + 1):
        for wd in window:
            try:
                d = wd.replace(year=y + (wd.year - 2027))
            except ValueError:  # Feb 29 in a non-leap year
                continue
            r = recs.get((d.year, d.month, d.day))
            if r is None:
                continue
            mx, mn, rain, snow = r
            if mx is None and mn is None and rain is None:
                continue
            n += 1
            if mx is not None:
                his.append(mx)
                if mx > 30.0:
                    hot += 1
            if mn is not None:
                los.append(mn)
            if rain is not None and rain > 0:
                rainy += 1
                rains.append(rain)
            if snow is not None and snow > 0:
                snowy += 1
    label_end = fri + dt.timedelta(days=2)
    return {
        "start": fri.isoformat(),
        "hi": round(st.mean(his), 1),
        "lo": round(st.mean(los), 1),
        "recHi": max(his),
        "recLo": min(los),
        "rainPct": round(100 * rainy / n),
        "rainTypical": round(st.median(rains), 1) if rains else 0,  # median, not mean (matches original; verified 2026-07-19)
        "snowPct": round(100 * snowy / n),
        "hotPct": round(100 * hot / max(len(his), 1), 1),  # denominator = days with a max-temp reading
        "nDays": n,
    }


def read_data_constant(src: str, name: str = "DATA_FULL"):
    marker = f"const {name} = ["
    start = src.index(marker)
    end = src.index("];", start)
    a = start + len(marker) - 1
    return json.loads(src[a : end + 1]), a, end + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="patch index.html DATA in place")
    ap.add_argument("--fetch", action="store_true", help="download missing yearly CSVs first")
    ap.add_argument("--last-year", type=int, default=2026, help="last data year to include")
    ap.add_argument("--era", choices=["full", "recent"], default="full",
                    help="which DATA constant to check/patch (full: 1913-, recent: 1990- averages; records always full-record)")
    args = ap.parse_args()

    if args.fetch:
        fetch_missing(args.last_year)

    global FIRST_YEAR
    era_start = {"full": 1913, "recent": 1990}[args.era]
    const_name = {"full": "DATA_FULL", "recent": "DATA_RECENT"}[args.era]
    recs = load_records()
    src = INDEX.read_text()
    data, a, b = read_data_constant(src, const_name)

    diffs = 0
    for entry in data:
        fri = dt.date.fromisoformat(entry["start"])
        FIRST_YEAR = 1913
        full = weekend_stats(recs, fri, args.last_year)
        FIRST_YEAR = era_start
        new = weekend_stats(recs, fri, args.last_year)
        new["recHi"], new["recLo"] = full["recHi"], full["recLo"]  # records always span the full record
        FIRST_YEAR = 1913
        for k, v in new.items():
            old = entry.get(k)
            if old is None or old != v:
                # tolerate float noise / off-by-a-few nDays from blank-record handling
                if old is not None:
                    # tolerances: blank-record handling (nDays), rounding edges (percent fields)
                    if k == "nDays" and abs(old - v) <= 0.06 * v:  # original counted "valid days" slightly more strictly
                        continue
                    if k in ("rainPct", "snowPct") and abs(old - v) <= 1:
                        continue
                    if isinstance(v, float) and abs(old - v) <= 0.2:
                        continue
                print(f"{entry['start']} {k}: {old} -> {v}")
                diffs += 1
                if args.write:
                    entry[k] = v

    if args.write:
        INDEX.write_text(src[:a] + json.dumps(data, ensure_ascii=False) + src[b:])
        print(f"wrote index.html ({diffs} field updates)")
    else:
        print(f"check complete: {diffs} fields differ (run with --write to apply)")


if __name__ == "__main__":
    main()
