# -*- coding: utf-8 -*-
"""Weekday DR dashboard update -> daily_build\\dashboard_data.json + dr_dashboard.html.

User spec (2026-11-06):
  * Mon-Fri 07:30, refresh ONLY: Closing Price (THB), P/E, P/BV, Dividend Yield
    and YTD Return.
  * Do NOT add DRs listed during the week - the DR list is the one from the last
    Saturday build.
  * Saturday 08:00 stays the full update (run_weekly.ps1), which re-bases this.

Everything else is carried over unchanged from the Saturday build's
dashboard_data.json, which this script only READS: DR list, names, conversion
ratio, analyst Target Price (THB) and its source, ETF/stock profiles, FX. Upside
is not refreshed as such, but it is derived (Target / Close - 1), so it is
recomputed against the new close - otherwise the page would show an upside that
contradicts its own two neighbouring columns.

Sources are the weekly build's own functions (imported, not copied):
  close   - SET, fetch_dr_data() + the same last/prior/carry-forward rule
  metrics - Yahoo, then Google Finance (P/E, div yield), then TradingView
  YTD     - fetch_ytd.py (underlying's own closes, user rule 2026-10-07)
A metric that every source misses today keeps Saturday's value rather than
flickering to "-".

Writes only to daily_build\\; run_daily.ps1 then renders the PDF and Excel and
publishes. The weekly files are never touched.
"""
import io
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
BASE = HERE / "dashboard_data.json"        # the Saturday build - read only
OUTDIR = HERE / "daily_build"
sys.path.insert(0, str(HERE))

# Both modules re-wrap sys.stdout at import. A replaced wrapper that gets
# garbage-collected closes the shared buffer ("I/O operation on closed file"),
# so hold a reference to every wrapper they create.
_streams = [sys.stdout]
import build_dr_dashboard as bd           # noqa: E402  (main() is __name__-guarded)
_streams.append(sys.stdout)
import fetch_ytd                          # noqa: E402
_streams.append(sys.stdout)

METRICS = ("pe", "pbv", "divYield")


def main():
    base = json.loads(BASE.read_text(encoding="utf-8"))
    rows = [dict(r) for r in base["rows"]]
    print(f"Saturday base: {len(rows)} DRs, generated {base['generated']}")

    # ---- Closing price from SET (same rule as the weekly build_rows) --------
    print("Fetching DR prices from SET ...")
    payload = bd.fetch_dr_data()
    by_sym = {r["symbol"]: r for r in payload["data"]}
    date_cur = (payload.get("tradingDate") or "")[:10]
    date_asia = (payload.get("tradingDateAsia") or "")[:10]
    trading_date = (f"{date_cur} US/EU, {date_asia} Asia"
                    if date_asia and date_asia != date_cur else date_cur)

    new_listings = sorted(set(by_sym) - {r["symbol"] for r in rows})
    gone, unchanged_close = [], 0
    for r in rows:
        s = by_sym.get(r["symbol"])
        if s is None:                                  # delisted / suspended
            gone.append(r["symbol"])
            continue
        close = s.get("last") if s.get("last") not in (None, 0) else s.get("prior")
        if close in (None, 0):
            unchanged_close += 1                       # keep Saturday's close
            continue
        r["close"] = close
    print(f"  trading date {trading_date} · {len(new_listings)} new listing(s) "
          f"NOT added{(': ' + ', '.join(new_listings)) if new_listings else ''}")
    if gone:
        print(f"  not on SET today (kept with Saturday's close): {', '.join(gone)}")

    # ---- P/E, P/BV, dividend yield: Yahoo -> Google -> TradingView ----------
    tickers = sorted({r["yahoo"] for r in rows if r.get("yahoo")})
    print(f"Fetching Yahoo Finance metrics for {len(tickers)} underlyings ...")
    ydata = bd.fetch_yahoo(tickers)

    # local ticker + exchange for the fallbacks, from today's SET rows
    local = {}
    for r in rows:
        s = by_sym.get(r["symbol"])
        if s and r.get("yahoo") and r["yahoo"] not in local:
            lt = bd.local_ticker(s)
            if lt:
                local[r["yahoo"]] = (lt, s["underlyingExchange"], s.get("underlyingClassId"))

    gmap = {t: (lt, ex) for t, (lt, ex, _) in local.items()
            if (ydata.get(t) or {}).get("pe") is None
            or (ydata.get(t) or {}).get("divYield") is None}
    if gmap:
        print(f"Google Finance fallback for {len(gmap)} underlyings ...")
        for t, (_gt, _gccy, gpe, gdy) in bd.fetch_google(gmap).items():
            d = ydata.setdefault(t, {})
            if d.get("pe") is None and gpe is not None:
                d["pe"] = gpe
            if d.get("divYield") is None and gdy is not None:
                d["divYield"] = gdy

    tv_map = {t: (lt, ex) for t, (lt, ex, _) in local.items()
              if any((ydata.get(t) or {}).get(m) is None for m in METRICS)}
    if tv_map:
        print(f"TradingView fallback for {len(tv_map)} underlyings ...")
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(bd.fetch_tv_metrics, ex, lt): t for t, (lt, ex) in tv_map.items()}
            for f in as_completed(futs):
                t = futs[f]
                d = ydata.setdefault(t, {})
                for m, v in zip(METRICS, f.result()):
                    if d.get(m) is None and v is not None:
                        d[m] = v

    kept = 0
    for r in rows:
        d = ydata.get(r.get("yahoo")) or {}
        pbv = d.get("pbv")
        if pbv is not None and not (0.01 <= pbv <= 1000):   # same glitch filter as weekly
            pbv = None
        for m, v in (("pe", d.get("pe")), ("pbv", pbv), ("divYield", d.get("divYield"))):
            if v is not None:
                r[m] = v
            elif r.get(m) is not None:
                kept += 1                                    # keep Saturday's value
        # derived, so it must move with the close
        t, c = r.get("target_thb"), r.get("close")
        r["upside"] = ((t - c) / c * 100) if (t and c) else None
    print(f"  metrics refreshed; {kept} value(s) no source had today -> kept Saturday's")

    # ---- write the draft data ------------------------------------------------
    OUTDIR.mkdir(exist_ok=True)
    out = {"tradingDate": trading_date,
           "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "fx": base.get("fx", {}), "rows": rows,
           "mode": "daily", "base_generated": base["generated"],
           "new_listings_skipped": new_listings}
    (OUTDIR / "dashboard_data.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- YTD: the weekly fetcher, pointed at the draft folder -----------------
    # Seed with the weekly cache so last year's closes are not re-downloaded.
    if (HERE / "ytd.json").exists() and not (OUTDIR / "ytd.json").exists():
        shutil.copy(HERE / "ytd.json", OUTDIR / "ytd.json")
    fetch_ytd.SRC = OUTDIR / "dashboard_data.json"
    fetch_ytd.OUT = OUTDIR / "ytd.json"
    fetch_ytd.main()

    # ---- render the page from the draft folder -------------------------------
    env = dict(os.environ, DR_DASH_DATA=str(OUTDIR), DR_DASH_MODE="daily",
               PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, str(HERE / "make_dashboard_html.py")], env=env, check=True)
    print(f"OK: daily draft {len(rows)} DRs (trading date {trading_date}) -> "
          f"{OUTDIR / 'dr_dashboard.html'}")


if __name__ == "__main__":
    main()
