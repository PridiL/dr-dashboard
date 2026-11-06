# -*- coding: utf-8 -*-
"""YTD Return of each DR's UNDERLYING (e.g. NVDA for NVDA19) -> ytd.json.

YTD Return (%) = the underlying's latest close on or before the dashboard's
build date / its last close of the previous year - 1, in the underlying's own
currency (no FX). User's rule (2026-10-07): use the underlying stock/ETF's
closing price, not the DR's SET price - the DR series only starts at listing,
so a SET-price basis left most of the 2026-listed DRs blank.

Prices: Yahoo Finance daily closes (unadjusted "Close", i.e. a price return),
one batched download for all underlyings. The previous-year close is constant
for the year, so it is cached and only fetched for tickers not yet cached; the
latest close is refetched every run.
"""
import io
import json
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
SRC = HERE / "dashboard_data.json"
OUT = HERE / "ytd.json"


def closes(tickers, start, end):
    """ticker -> pandas Series of daily closes (NaN-free), start <= d < end."""
    out = {}
    if not tickers:
        return out
    for i in range(0, len(tickers), 80):                 # modest batches
        chunk = tickers[i:i + 80]
        df = yf.download(chunk, start=start, end=end, auto_adjust=False,
                         progress=False, threads=True, group_by="ticker")
        for t in chunk:
            try:
                s = (df[t]["Close"] if len(chunk) > 1 else df["Close"]).dropna()
                if isinstance(s, pd.DataFrame):          # single-ticker edge case
                    s = s.iloc[:, 0]
                if len(s):
                    out[t] = s
            except Exception:
                pass
    return out


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    tickers = sorted({r["yahoo"] for r in data["rows"] if r.get("yahoo")})
    # "Recent week close" = latest close up to the day the dashboard was built,
    # so the column lines up with every other number on the page.
    asof = datetime.strptime(data["generated"][:10], "%Y-%m-%d").date()
    year = asof.year

    bases = {}
    if OUT.exists():
        try:
            blob = json.loads(OUT.read_text(encoding="utf-8"))
            if blob.get("year") == year:
                bases = {t: v for t, v in blob.get("bases", {}).items() if v.get("close")}
        except Exception:
            pass

    # previous-year close: last bar of December (any market's final session)
    need = [t for t in tickers if t not in bases]
    print(f"{len(tickers)} underlyings · {len(bases)} cached {year - 1} closes · "
          f"fetching {len(need)} bases + all latest closes (as of {asof})")
    for t, s in closes(need, f"{year - 1}-12-01", f"{year}-01-01").items():
        bases[t] = {"close": round(float(s.iloc[-1]), 6),
                    "date": s.index[-1].strftime("%Y-%m-%d")}

    # latest close on or before the build date (look back two weeks for holidays)
    latest = {}
    for t, s in closes(tickers, (asof - timedelta(days=14)).isoformat(),
                       (asof + timedelta(days=1)).isoformat()).items():
        latest[t] = {"close": round(float(s.iloc[-1]), 6),
                     "date": s.index[-1].strftime("%Y-%m-%d")}

    ytd = {}
    for t in tickers:
        b, l = bases.get(t), latest.get(t)
        if b and l and b["close"]:
            ytd[t] = round((l["close"] / b["close"] - 1) * 100, 4)

    OUT.write_text(json.dumps(
        {"year": year, "asof": asof.isoformat(), "bases": bases,
         "latest": latest, "ytd": ytd},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    missing = [t for t in tickers if t not in ytd]
    rows_missing = sum(1 for r in data["rows"] if r.get("yahoo") not in ytd)
    print(f"YTD for {len(ytd)}/{len(tickers)} underlyings "
          f"({len(data['rows']) - rows_missing}/{len(data['rows'])} DR rows)")
    for t in missing:
        why = ("no %d close (listed later)" % (year - 1)) if t in latest and t not in bases \
            else "no recent close" if t in bases else "no Yahoo price data"
        print(f"  missing {t:14} {why}")


if __name__ == "__main__":
    main()
