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


def tv_closes(symbols):
    """DR symbol -> {"close", "date"} from TradingView's SET quote (SET:<DR>).

    Public scanner endpoint, no login; the same quote the user's DR watchlists
    show. "date" is the Bangkok date of the last trade, so a 07:30 run reports
    the previous session. DRs TradingView does not list (e.g. ALAB01) are absent.
    """
    import requests
    from datetime import timezone, timedelta
    bkk = timezone(timedelta(hours=7))
    out = {}
    for i in range(0, len(symbols), 250):
        chunk = symbols[i:i + 250]
        body = {"symbols": {"tickers": [f"SET:{s}" for s in chunk]},
                "columns": ["close", "last_bar_update_time"]}
        for attempt in range(3):
            try:
                r = requests.post("https://scanner.tradingview.com/thailand/scan",
                                  json=body, timeout=30,
                                  headers={"User-Agent": bd.UA, "Content-Type": "application/json"})
                r.raise_for_status()
                for item in r.json().get("data", []):
                    close, ts = (item.get("d") or [None, None])[:2]
                    if close:
                        out[item["s"].split(":", 1)[1]] = {
                            "close": close,
                            "date": datetime.fromtimestamp(ts, bkk).strftime("%Y-%m-%d") if ts else None,
                        }
                break
            except Exception as e:
                print(f"  TradingView quote batch failed ({type(e).__name__}) - retry {attempt + 1}")
    return out


def main():
    base = json.loads(BASE.read_text(encoding="utf-8"))
    rows = [dict(r) for r in base["rows"]]
    print(f"Saturday base: {len(rows)} DRs, generated {base['generated']}")

    # ---- Closing price: set.or.th first, TradingView as the contingency ------
    # (user rule 2026-11-06). SET's own DR feed is the source; if it cannot be
    # read at all (blocked, down, bad payload) - or a DR is missing from it - the
    # price comes from TradingView's SET:<DR> quote, i.e. exactly what the user's
    # "DR Watchlist US" / "DR Watchlist Asia & Europe" lists display (those lists
    # are built from these same SET:<DR> symbols). Public quote, no login.
    print("Fetching DR prices from SET ...")
    payload, set_error = {"data": []}, None
    try:
        if os.environ.get("DR_TEST_SET_DOWN"):        # test switch for the fallback
            raise RuntimeError("simulated set.or.th outage (DR_TEST_SET_DOWN)")
        payload = bd.fetch_dr_data()
        if not payload.get("data"):
            raise RuntimeError("set.or.th returned no DR rows")
    except Exception as e:
        set_error = f"{type(e).__name__}: {str(e)[:160]}"
        payload = {"data": []}
        print(f"  set.or.th FAILED ({set_error}) - falling back to TradingView for every DR")
    by_sym = {r["symbol"]: r for r in payload["data"]}
    date_cur = (payload.get("tradingDate") or "")[:10]
    date_asia = (payload.get("tradingDateAsia") or "")[:10]
    trading_date = (f"{date_cur} US/EU, {date_asia} Asia"
                    if date_asia and date_asia != date_cur else date_cur)

    new_listings = sorted(set(by_sym) - {r["symbol"] for r in rows})
    need_tv, n_set = [], 0
    for r in rows:
        s = by_sym.get(r["symbol"])
        close = None
        if s is not None:
            close = s.get("last") if s.get("last") not in (None, 0) else s.get("prior")
        if close in (None, 0):
            need_tv.append(r)                          # SET down, or no price for it
            continue
        r["close"] = close
        n_set += 1

    n_tv = n_kept = 0
    if need_tv:
        tv = tv_closes([r["symbol"] for r in need_tv])
        for r in need_tv:
            q = tv.get(r["symbol"])
            if q:
                r["close"] = q["close"]
                n_tv += 1
            else:
                n_kept += 1                            # keep the last known close
        if not by_sym:                                 # whole-source fallback: date from TV
            dates = sorted({q["date"] for q in tv.values() if q.get("date")})
            trading_date = (dates[-1] + " (TradingView)") if dates else "unknown (TradingView)"
        kept = [r["symbol"] for r in need_tv if r["symbol"] not in tv]
        print(f"  TradingView priced {n_tv}/{len(need_tv)} DR(s) set.or.th did not"
              + (f"; kept last close for {len(kept)}: {', '.join(kept[:15])}" if kept else ""))
    price_note = ("set.or.th" if not need_tv else
                  f"TradingView (set.or.th unavailable: {set_error})" if not by_sym else
                  f"set.or.th, TradingView for {n_tv} DR(s) set.or.th had no price for")
    print(f"  closing prices: {n_set} set.or.th · {n_tv} TradingView · {n_kept} kept from last update")
    print(f"  trading date {trading_date} · {len(new_listings)} new listing(s) "
          f"NOT added{(': ' + ', '.join(new_listings)) if new_listings else ''}")

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
           "new_listings_skipped": new_listings,
           "price_source": price_note,
           "price_counts": {"set": n_set, "tradingview": n_tv, "kept": n_kept}}
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
