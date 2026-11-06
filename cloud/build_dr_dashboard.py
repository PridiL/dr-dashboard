# -*- coding: utf-8 -*-
"""
Build a DR dashboard for all SET DRs, grouped by country of the underlying.

Data sources:
- DR list, close prices (THB), conversion ratios: SET website API
  (https://www.set.or.th/th/market/product/dr/marketdata), fetched via
  Playwright headless Chromium because the site is behind Incapsula.
- Analyst target price, P/E, P/BV, dividend yield, company names and
  local-currency-to-THB FX rates: Yahoo Finance (via yfinance).

Target Price (THB) = target price in local currency x (local->THB FX) / conversion ratio
Upside (%)         = (Target Price THB - DR close THB) / DR close THB x 100

Outputs (in this script's folder):
- DR_Dashboard_YYYY-MM-DD.xlsx
- dashboard_data.json  (for rendering the HTML dashboard)
"""
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
import yfinance as yf
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
PAGE_URL = "https://www.set.or.th/th/market/product/dr/marketdata"
# SET exposes two trading dates. C ("current") is the next/in-progress session, P
# ("previous") is the last completed one. Neither alone is right for every market:
#   - Asian underlyings (HK/JP/VN/CN/SG/TW) trade in SET's DAY session, so on a
#     Saturday run C points at Monday, where that session has not happened yet and
#     `last` AND `prior` are both null -> 205 of 493 rows showed "-" for close.
#   - US/European underlyings trade in SET's NIGHT session, so their freshest prints
#     (Friday night Thai time) are already booked by SET to the *next* trading date,
#     i.e. they live under C, not P. Taking P for these would show a staler price.
# So we fetch both and take Asian rows from P, US/EU rows from C. Fixed 2026-08-01.
API_TMPL = "/api/set/dr/search?tradeDateType={}&lang=th"

# Countries whose underlying market trades during SET's day session; these rows are
# taken from the previous (completed) trading date. Everything else comes from C.
ASIA_COUNTRIES = {"HONG KONG", "JAPAN", "VIETNAM", "SINGAPORE", "CHINA", "TAIWAN"}

# Per-row session fields replaced wholesale when an Asian row is taken from P, so a
# row never mixes price fields from two different trading dates.
PRICE_FIELDS = ("prior", "open", "high", "low", "last", "change", "percentChange",
                "totalVolume", "totalValue", "bid", "offer", "marketStatus")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# exchange -> (country, flag emoji, yahoo suffix, default currency)
EXCH = {
    "The Nasdaq Stock Market":                 ("UNITED STATES", "\U0001F1FA\U0001F1F8", "",    "USD"),
    "The Nasdaq Global Select Market":         ("UNITED STATES", "\U0001F1FA\U0001F1F8", "",    "USD"),
    "The New York Stock Exchange":             ("UNITED STATES", "\U0001F1FA\U0001F1F8", "",    "USD"),
    "The New York Stock Exchange Archipelago": ("UNITED STATES", "\U0001F1FA\U0001F1F8", "",    "USD"),
    "The Stock Exchange of Hong Kong Limited": ("HONG KONG",     "\U0001F1ED\U0001F1F0", ".HK", "HKD"),
    "Tokyo Stock Exchange":                    ("JAPAN",         "\U0001F1EF\U0001F1F5", ".T",  "JPY"),
    "Hochiminh Stock Exchange":                ("VIETNAM",       "\U0001F1FB\U0001F1F3", ".VN", "VND"),
    "Singapore Exchange":                      ("SINGAPORE",     "\U0001F1F8\U0001F1EC", ".SI", "SGD"),
    "Shanghai Stock Exchange":                 ("CHINA",         "\U0001F1E8\U0001F1F3", ".SS", "CNY"),
    "Shenzhen Stock Exchange":                 ("CHINA",         "\U0001F1E8\U0001F1F3", ".SZ", "CNY"),
    "Euronext Paris":                          ("FRANCE",        "\U0001F1EB\U0001F1F7", ".PA", "EUR"),
    "Euronext Amsterdam":                      ("NETHERLANDS",   "\U0001F1F3\U0001F1F1", ".AS", "EUR"),
    "Euronext Milan":                          ("ITALY",         "\U0001F1EE\U0001F1F9", ".MI", "EUR"),
    "Deutsche Borse":                          ("GERMANY",       "\U0001F1E9\U0001F1EA", ".DE", "EUR"),
    "Nasdaq Copenhagen":                       ("DENMARK",       "\U0001F1E9\U0001F1F0", ".CO", "DKK"),
    "Taiwan Stock Exchange":                   ("TAIWAN",        "\U0001F1F9\U0001F1FC", ".TW", "TWD"),
}

# (underlying, exchange) -> local ticker, for cases the regexes can't resolve
MANUAL_TICKER = {
    ("DCVFMVN30 ETF", "Hochiminh Stock Exchange"): "E1VFVN30",
    ("DCVFMVN DIAMOND ETF", "Hochiminh Stock Exchange"): "FUEVFVND",
}

# (underlying, exchange) -> full Yahoo ticker, when suffix rules don't apply
MANUAL_YAHOO = {
    # SET calls it SMSWLD but Yahoo lists Invesco MSCI World UCITS ETF as SC0J on Xetra
    ("WORLDA ETF", "Deutsche Borse"): "SC0J.DE",
}

# Target-price source priority (per user rule / dashboard feedback):
#   1) Google Finance  2) Yahoo Finance  3) TradingView
# Google Finance only publishes an analyst "12-month forecast" (Highest /
# Average / Lowest) for US-listed stocks; non-US exchanges load valid quotes
# but carry no target section, so in practice Google covers US names, Yahoo
# covers the rest of the world, and TradingView catches China A-shares etc.

# SET exchange -> Google Finance exchange code(s) to try, in order.
GF_EXCH = {
    "The Nasdaq Stock Market":                 ["NASDAQ", "NYSE", "NYSEARCA"],
    "The Nasdaq Global Select Market":         ["NASDAQ", "NYSE", "NYSEARCA"],
    "The New York Stock Exchange":             ["NYSE", "NASDAQ", "NYSEARCA"],
    "The New York Stock Exchange Archipelago": ["NYSEARCA", "NYSE", "NASDAQ"],
    "The Stock Exchange of Hong Kong Limited": ["HKG"],
    "Tokyo Stock Exchange":                    ["TYO"],
    "Hochiminh Stock Exchange":                ["HCM"],
    "Singapore Exchange":                      ["SGX"],
    "Shanghai Stock Exchange":                 ["SHA"],
    "Shenzhen Stock Exchange":                 ["SHE"],
    "Euronext Paris":                          ["EPA"],
    "Euronext Amsterdam":                      ["AMS"],
    "Euronext Milan":                          ["BIT"],
    "Deutsche Borse":                          ["ETR"],
    "Nasdaq Copenhagen":                       ["CPH"],
    "Taiwan Stock Exchange":                   ["TPE"],
}

# Currency symbol shown in the Google Finance forecast -> ISO code. Ambiguous
# glyphs (¥ = JPY or CNY) map to None so the value is rejected rather than
# guessed; the target then falls through to Yahoo.
GF_SYMBOL_CCY = {
    "$": "USD", "US$": "USD", "HK$": "HKD", "€": "EUR", "₫": "VND",
    "NT$": "TWD", "S$": "SGD", "SGD": "SGD", "kr": "DKK", "DKK": "DKK",
    "£": "GBP", "¥": None,
}


def _gf_stat(h, label):
    """Numeric stat labelled `label` on a Google Finance quote page.
    Returns the float value (percent stats returned as their percent number),
    or None."""
    i = h.find(">" + label + "</div>")
    if i < 0:
        return None
    m = re.search(r">\s*([\d,]+\.?\d*)\s*%?\s*<", h[i:i + 260])
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_google_target(local, exchange):
    """From a Google Finance quote page: (target, ccy, pe, divyield).

    - target: analyst 12-month AVERAGE, LOCAL currency, only accepted when the
      forecast currency matches the exchange's expected currency (so a mis-scraped
      number never corrupts the THB conversion). None if absent/mismatched.
    - pe / divyield (% per year): the page's own "P/E ratio" and "Dividend yield"
      stats — unitless, so returned regardless of the target currency check.
    Any element may be None.
    """
    codes = GF_EXCH.get(exchange)
    if not codes or not local:
        return None, None, None, None
    expected = EXCH[exchange][3]
    fb_pe = fb_dy = None
    seen_page = False
    for code in codes:
        try:
            r = requests.get(
                f"https://www.google.com/finance/quote/{local}:{code}",
                headers={"User-Agent": UA}, timeout=20)
            if not r.ok:
                continue
            h = r.text
            pe = _gf_stat(h, "P/E ratio")
            if pe is not None and pe <= 0:
                pe = None
            dy = _gf_stat(h, "Dividend yield")
            if not seen_page:
                fb_pe, fb_dy, seen_page = pe, dy, True
            i = h.find(">Average</div>")
            if i >= 0:
                m = re.search(r"<span>\s*([^\d<]*?)([\d,]+\.?\d*)\s*</span>",
                              h[i:i + 400])
                if m:
                    sym = m.group(1).strip()
                    val = float(m.group(2).replace(",", ""))
                    ccy = GF_SYMBOL_CCY.get(sym)
                    if ccy is None and sym == "$" and expected == "USD":
                        ccy = "USD"
                    if ccy and ccy == expected and val > 0:
                        return val, ccy, (pe if pe is not None else fb_pe), \
                               (dy if dy is not None else fb_dy)
        except Exception:
            pass
    return None, None, fb_pe, fb_dy


def fetch_google(gmap):
    """{yahoo_ticker: (local, exchange)} -> {yahoo_ticker: (target, ccy, pe, divyield)}."""
    out = {}

    def one(item):
        yt, (local, ex) = item
        return yt, fetch_google_target(local, ex)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, it) for it in gmap.items()]
        for i, f in enumerate(as_completed(futures), 1):
            yt, res = f.result()
            out[yt] = res
            if i % 40 == 0:
                print(f"  google {i}/{len(gmap)}")
    return out


# TradingView exchange prefixes, used as a target-price fallback for STOCK
# underlyings Yahoo has no analyst target for (mainly China A-shares).
# US entries are None: try NASDAQ/NYSE/AMEX in order.
TV_PREFIX = {
    "The Nasdaq Stock Market": None,
    "The Nasdaq Global Select Market": None,
    "The New York Stock Exchange": None,
    "The New York Stock Exchange Archipelago": None,
    "The Stock Exchange of Hong Kong Limited": "HKEX",
    "Tokyo Stock Exchange": "TSE",
    "Hochiminh Stock Exchange": "HOSE",
    "Singapore Exchange": "SGX",
    "Shanghai Stock Exchange": "SSE",
    "Shenzhen Stock Exchange": "SZSE",
    "Euronext Paris": "EURONEXT",
    "Euronext Amsterdam": "EURONEXT",
    "Euronext Milan": "MIL",
    "Deutsche Borse": "XETR",
    "Nasdaq Copenhagen": "OMXCOP",
    "Taiwan Stock Exchange": "TWSE",
}


def fetch_tv_target(exchange, local):
    """Analyst 1-year consensus target from TradingView, in LOCAL currency.

    Note: TradingView's price_target_average is USD-denominated; price_target_1y
    is in the instrument's own currency, which is what we need here.
    """
    if exchange not in TV_PREFIX:
        return None, None
    prefix = TV_PREFIX[exchange]
    if prefix == "HKEX":
        local = local.lstrip("0") or "0"
    elif prefix == "OMXCOP":
        local = local.replace("-", "_").replace(".", "_")
    prefixes = [prefix] if prefix else ["NASDAQ", "NYSE", "AMEX"]
    for p in prefixes:
        try:
            r = requests.get(
                "https://scanner.tradingview.com/symbol",
                params={"symbol": f"{p}:{local}",
                        "fields": "price_target_1y,currency", "no_404": "true"},
                headers={"User-Agent": UA}, timeout=20)
            if r.ok:
                d = r.json()
                if d.get("price_target_1y"):
                    return float(d["price_target_1y"]), d.get("currency")
        except Exception:
            pass
    return None, None


def fetch_tv_metrics(exchange, local):
    """(pe, pbv, divyield%) from TradingView, or (None, None, None). These are
    unitless ratios (dividends_yield already in percent), so no currency check is
    needed. Used to fill P/E / P/BV / dividend yield that Yahoo (and Google) lack."""
    if exchange not in TV_PREFIX:
        return None, None, None
    prefix = TV_PREFIX[exchange]
    if prefix == "HKEX":
        local = local.lstrip("0") or "0"
    elif prefix == "OMXCOP":
        local = local.replace("-", "_").replace(".", "_")
    prefixes = [prefix] if prefix else ["NASDAQ", "NYSE", "AMEX"]
    for p in prefixes:
        try:
            r = requests.get(
                "https://scanner.tradingview.com/symbol",
                params={"symbol": f"{p}:{local}",
                        "fields": "price_earnings_ttm,price_book_fq,dividends_yield",
                        "no_404": "true"},
                headers={"User-Agent": UA}, timeout=20)
            if r.ok:
                d = r.json()
                pe = d.get("price_earnings_ttm")
                pbv = d.get("price_book_fq")
                dy = d.get("dividends_yield")
                if any(v is not None for v in (pe, pbv, dy)):
                    return ((pe if pe and pe > 0 else None),
                            (pbv if pbv and pbv > 0 else None), dy)
        except Exception:
            pass
    return None, None, None


def fetch_dr_data():
    """Fetch both trading dates and merge: Asian rows from P, US/EU rows from C.

    Returns the C payload with Asian rows' price fields swapped in from P, plus
    tradingDateAsia so callers can label the two dates.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="th-TH",
                                  viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)

        def grab(date_type):
            return page.evaluate(
                "url => fetch(url, {headers: {accept: 'application/json'}})"
                ".then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })",
                API_TMPL.format(date_type),
            )

        cur, prev = grab("C"), grab("P")
        browser.close()

    for name, pl in (("C", cur), ("P", prev)):
        if not isinstance(pl, dict) or "data" not in pl:
            raise RuntimeError(f"Unexpected SET API payload for tradeDateType={name}")

    prev_by_symbol = {r["symbol"]: r for r in prev["data"]}
    swapped = missing = 0
    for r in cur["data"]:
        country = EXCH.get(r.get("underlyingExchange"), (None,))[0]
        if country not in ASIA_COUNTRIES:
            continue
        src = prev_by_symbol.get(r["symbol"])
        if src is None:                  # listed today; no previous session to take
            missing += 1
            continue
        for f in PRICE_FIELDS:
            r[f] = src.get(f)
        swapped += 1

    cur["tradingDateAsia"] = prev.get("tradingDate")
    print(f"  Asian rows taken from previous trading date: {swapped}"
          + (f" ({missing} had no previous-session row)" if missing else ""))
    return cur


def local_ticker(r):
    key = (r["underlying"], r["underlyingExchange"])
    if key in MANUAL_TICKER:
        return MANUAL_TICKER[key]
    name, url = r["underlyingName"], r.get("underlyingUrl") or ""
    m = re.search(r"\(([A-Z0-9\-\.]{1,12})\)\s*$", name)
    if m:
        return m.group(1)
    for pat in [r"nasdaq\.com/market-activity/(?:stocks|etf)/([a-z0-9\-\.]+)",
                r"nyse\.com/quote/\w+:([A-Z0-9\-\.]+)",
                r"[?&]sym=(\d+)", r"topSearchStr=(\d+)",
                r"STOCK_CODE=(\d+)", r"[?&]code=([A-Z0-9]+)",
                r"etfInfo/(\w+)", r"/(\d{6})/detail"]:
        m = re.search(pat, url)
        if m:
            return m.group(1).upper()
    return None


def yahoo_ticker(r):
    ex = r["underlyingExchange"]
    key = (r["underlying"], ex)
    if key in MANUAL_YAHOO:
        return MANUAL_YAHOO[key]
    t = local_ticker(r)
    if not t:
        return None
    _, _, suffix, _ = EXCH[ex]
    if suffix == "":                      # US: Yahoo uses '-' for share classes
        t = t.replace(".", "-")
    elif suffix == ".HK":                 # some SET names embed '.HK' already
        t = re.sub(r"\.HK$", "", t, flags=re.I).zfill(4)
    return t + suffix


def ratio_value(text):
    """SET's '3,000 : 1' -> 3000.0 (DR per 1 underlying)."""
    m = re.match(r"\s*([\d,.]+)\s*:\s*([\d,.]+)\s*$", str(text or ""))
    if not m:
        return None
    try:
        left = float(m.group(1).replace(",", ""))
        right = float(m.group(2).replace(",", ""))
        return left / right if right else None
    except ValueError:
        return None


def clean_name(r):
    n = r["underlyingName"]
    n = re.sub(r"^(บริษัท|หุ้นสามัญของบริษัท|โครงการจัดการลงทุนต่างประเทศ)\s*", "", n)
    n = re.sub(r"\s*\([A-Z0-9\-\.]{1,12}\)\s*$", "", n)
    return n.strip()


def fetch_yahoo(tickers):
    """ticker -> dict of fields; missing/failed tickers map to {}."""
    out = {}

    def one(t):
        for _ in range(3):
            try:
                info = yf.Ticker(t).info
                if info and (info.get("currency") or info.get("regularMarketPrice") is not None):
                    # Annual (per-year) dividend yield in %. Yahoo's dividendYield
                    # is the annualized forward yield already expressed in percent;
                    # fall back to trailing-12-month yield (a fraction) when absent.
                    dy = info.get("dividendYield")
                    if dy is None and info.get("trailingAnnualDividendYield") is not None:
                        dy = info["trailingAnnualDividendYield"] * 100
                    return t, {
                        "currency": info.get("currency"),
                        "target": info.get("targetMeanPrice"),
                        "pe": info.get("trailingPE"),
                        "pbv": info.get("priceToBook"),
                        "divYield": dy,
                        "longName": info.get("longName") or info.get("shortName"),
                    }
            except Exception:
                pass
        return t, {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one, t) for t in tickers]
        for i, f in enumerate(as_completed(futures), 1):
            t, d = f.result()
            out[t] = d
            if i % 40 == 0:
                print(f"  yahoo {i}/{len(tickers)}")
    return out


def fetch_fx(currencies):
    fx = {"THB": 1.0}
    for c in currencies:
        if c in fx or not c:
            continue
        pair = "THB=X" if c == "USD" else f"{c}THB=X"
        try:
            info = yf.Ticker(pair).info
            rate = info.get("regularMarketPrice") or info.get("previousClose")
            if rate:
                fx[c] = float(rate)
        except Exception:
            pass
    return fx


def _prev_closes():
    """{symbol: close} from the previous dashboard_data.json. SET returns null
    last/prior for markets that haven't traded yet on a given trade date — e.g.
    all the Asian-exchange DRs on a weekend / pre-open snapshot, which is exactly
    when the Saturday job runs — so we carry the last known close forward rather
    than showing a blank close."""
    prev = HERE / "dashboard_data.json"
    out = {}
    if prev.exists():
        try:
            for pr in json.loads(prev.read_text(encoding="utf-8")).get("rows", []):
                if pr.get("close") is not None:
                    out[pr["symbol"]] = pr["close"]
        except Exception:
            pass
    return out


def build_rows(payload, ymap, ydata, fx):
    prev_close = _prev_closes()
    rows = []
    carried = 0
    for r in payload["data"]:
        ex = r["underlyingExchange"]
        country, flag, _, def_ccy = EXCH[ex]
        yt = ymap.get((r["underlying"], ex))
        d = ydata.get(yt, {}) if yt else {}
        ccy = d.get("currency") or def_ccy
        # LSE-style pence quotes (not currently present, but guard anyway)
        scale = 0.01 if ccy == "GBp" else 1.0
        if ccy == "GBp":
            ccy = "GBP"

        close = r.get("last") if r.get("last") not in (None, 0) else r.get("prior")
        if close in (None, 0):                       # not-yet-traded: carry last close forward
            close = prev_close.get(r["symbol"])
            if close is not None:
                carried += 1
        rv = ratio_value(r.get("conversionRatio"))
        target_local = d.get("target")
        rate = fx.get(ccy)
        target_thb = (target_local * scale * rate / rv) if (target_local and rate and rv) else None
        upside = ((target_thb - close) / close * 100) if (target_thb and close) else None
        pbv = d.get("pbv")
        if pbv is not None and not (0.01 <= pbv <= 1000):   # Yahoo unit glitches (e.g. BRK-B)
            pbv = None
        ratio_disp = ("1:" + format(int(rv), ",")) if rv and rv == int(rv) else \
                     ("1:" + str(rv)) if rv else (r.get("conversionRatio") or "-")

        rows.append({
            "country": country, "flag": flag,
            "symbol": r["symbol"],
            "underlying": d.get("longName") or clean_name(r),
            "close": close, "ratio": ratio_disp,
            "target_thb": target_thb, "upside": upside,
            "pe": d.get("pe"), "pbv": pbv, "divYield": d.get("divYield"),
            "yahoo": yt, "currency": ccy,
            "is_etf": r.get("underlyingClassId") == "7",
            "target_src": (d.get("target_src") or "Yahoo Finance") if target_thb else None,
        })
    if carried:
        print(f"  carried forward last-known close for {carried} DR(s) with no SET "
              f"last/prior (not-yet-traded markets)")
    rows.sort(key=lambda x: (x["country"], x["symbol"]))
    return rows


# ISO currency code -> full name, for the Excel exchange-rate disclaimer.
CCY_NAME_XL = {
    "USD": "US Dollar", "EUR": "Euro", "HKD": "Hong Kong Dollar",
    "JPY": "Japanese Yen", "CNY": "Chinese Yuan", "SGD": "Singapore Dollar",
    "VND": "Vietnamese Dong", "TWD": "Taiwan Dollar", "DKK": "Danish Krone",
    "GBP": "Pound Sterling", "THB": "Thai Baht",
}


def write_excel(rows, trading_date, fx=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "DR Dashboard"

    ws["A1"] = "DR Dashboard — SET Depositary Receipts"
    ws["A1"].font = Font(name="Arial", size=14, bold=True, color="1F3864")
    ws["A2"] = (f"Close price & ratio: set.or.th (trading date {trading_date})  |  "
                f"Target price source priority: Google Finance > Yahoo Finance > TradingView  |  "
                f"P/E / P/BV / Div. yield: Yahoo Finance analyst consensus  |  "
                f"Target Price (THB) = local target x FX(THB) / conversion ratio  |  "
                f"Generated {datetime.now():%Y-%m-%d %H:%M}")
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")

    headers = [("DR", 14), ("Underlying Asset", 38), ("Close Price\n(THB)", 12),
               ("Conversion\nRatio", 12), ("Target Price\n(THB)", 12), ("Source", 14),
               ("Upside\n(%)", 10), ("P/E", 9), ("P/BV", 9),
               ("Dividend Yield\n(% per year)", 13)]
    HEAD_FILL = PatternFill("solid", fgColor="1F4E9C")
    thin = Side(style="thin", color="C9C9C9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hrow = 4
    for col, (title, width) in enumerate(headers, start=1):
        c = ws.cell(row=hrow, column=col, value=title)
        c.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = border
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[hrow].height = 30

    def put(rix, col, v, fmt=None, bold=False, center=True, fill=None):
        c = ws.cell(row=rix, column=col, value=v if v is not None else "-")
        c.font = Font(name="Arial", size=10, bold=bold)
        c.border = border
        c.alignment = Alignment(horizontal="center" if center else "left", vertical="center")
        if fmt and v is not None:
            c.number_format = fmt
        if fill:
            c.fill = fill
        return c

    GROUP_FILL = PatternFill("solid", fgColor="EDF1F8")
    rix = hrow
    country = None
    for r in rows:
        if r["country"] != country:
            country = r["country"]
            rix += 1
            for col in range(1, len(headers) + 1):
                ws.cell(row=rix, column=col).fill = GROUP_FILL
                ws.cell(row=rix, column=col).border = border
            c = ws.cell(row=rix, column=2, value=country)
            c.font = Font(name="Arial", size=10, bold=True, color="1F3864")
            c.alignment = Alignment(horizontal="left", vertical="center")
            ws.row_dimensions[rix].height = 20
        rix += 1
        put(rix, 1, r["symbol"], bold=True)
        put(rix, 2, r["underlying"], center=False)
        put(rix, 3, r["close"], "#,##0.00")
        put(rix, 4, r["ratio"])
        put(rix, 5, r["target_thb"], "#,##0.00")
        put(rix, 6, r["target_src"])
        put(rix, 7, r["upside"], "#,##0.00")
        put(rix, 8, r["pe"], "#,##0.00")
        put(rix, 9, r["pbv"], "#,##0.00")
        put(rix, 10, r["divYield"], "#,##0.00")

    ws.freeze_panes = f"A{hrow + 1}"

    # --- Disclaimers below the table ---
    ncol = len(headers)
    by_src = {}
    for r in rows:
        if r["target_src"]:
            by_src[r["target_src"]] = by_src.get(r["target_src"], 0) + 1
    fx = fx or {}
    fx_line = "   ".join(
        f"1 {c} = {format(v, '.6g')} THB ({CCY_NAME_XL.get(c, c)})"
        for c, v in sorted(fx.items()) if c != "THB")

    disc_head = Font(name="Arial", size=10, bold=True, color="1F3864")
    disc_body = Font(name="Arial", size=9, color="444444")

    def disc(rix, text, font):
        ws.merge_cells(start_row=rix, start_column=1, end_row=rix, end_column=ncol)
        c = ws.cell(row=rix, column=1, value=text)
        c.font = font
        c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        ws.row_dimensions[rix].height = 42

    rix += 2
    disc(rix, "DISCLAIMERS", disc_head); ws.row_dimensions[rix].height = 16
    rix += 1
    disc(rix, "Analyst target price — sources & methodology", disc_head)
    ws.row_dimensions[rix].height = 16
    rix += 1
    disc(rix,
         "Target Price (THB) is derived from a published analyst CONSENSUS target for the "
         "underlying, used in priority order: (1) Google Finance — Average of Wall Street "
         "analysts' 12-month targets (mainly US listings); (2) Yahoo Finance — mean analyst "
         "12-month target (targetMeanPrice); (3) TradingView — 1-year analyst consensus, used "
         "only where the above have none (e.g. China A-shares).  "
         f"This file: {', '.join(f'{k}: {v}' for k, v in sorted(by_src.items()))}.",
         disc_body)
    rix += 1
    disc(rix,
         "Brokerage note: each figure is an AGGREGATED CONSENSUS of price targets from multiple "
         "sell-side brokerages / research houses covering the stock. Google Finance, Yahoo Finance "
         "and TradingView publish only the aggregate and do NOT disclose the individual brokerages "
         "behind each target, so specific broker names cannot be attributed here. ETFs and newly "
         "listed DRs typically have no analyst coverage. Targets are opinions, not guarantees, and "
         "change frequently.", disc_body)
    rix += 1
    disc(rix, "Exchange rates used in the calculation", disc_head)
    ws.row_dimensions[rix].height = 16
    rix += 1
    disc(rix,
         "Target Price (THB) = local-currency target x rate to THB / conversion ratio. The rates "
         "below are INDICATIVE spot mid-market rates retrieved from Yahoo Finance at generation "
         f"time and used solely to express foreign-currency targets in THB:  {fx_line}",
         disc_body)
    rix += 1
    disc(rix,
         "These rates fluctuate continuously and may differ from rates actually available for "
         "conversion, from the DR issuer's conversion/settlement rate, and from interbank or retail "
         "rates. Indicative only — not a quote or an offer to transact. Not investment advice.",
         disc_body)

    out = HERE / f"DR_Dashboard_{datetime.now():%Y-%m-%d}.xlsx"
    wb.save(out)
    return out


if __name__ == "__main__":
    print("Fetching DR list from SET ...")
    payload = fetch_dr_data()
    drs = payload["data"]
    date_cur = (payload.get("tradingDate") or "")[:10]
    date_asia = (payload.get("tradingDateAsia") or "")[:10]
    # Rows come from two different SET trading dates (see fetch_dr_data), so label
    # both rather than implying one date covers every market.
    trading_date = (f"{date_cur} US/EU, {date_asia} Asia"
                    if date_asia and date_asia != date_cur else date_cur)
    print(f"  {len(drs)} DRs, trading date {trading_date}")

    ymap = {}
    for r in drs:
        key = (r["underlying"], r["underlyingExchange"])
        if key not in ymap:
            ymap[key] = yahoo_ticker(r)
    unmapped = sorted({u for (u, ex), t in ymap.items() if not t})
    if unmapped:
        print(f"WARNING: no Yahoo ticker for {len(unmapped)} underlyings "
              f"(new listings? add to MANUAL_TICKER/MANUAL_YAHOO): {unmapped}")
    tickers = sorted({t for t in ymap.values() if t})
    print(f"Fetching Yahoo Finance data for {len(tickers)} underlyings ...")
    ydata = fetch_yahoo(tickers)
    ok = sum(1 for v in ydata.values() if v)
    print(f"  got data for {ok}/{len(tickers)}")
    dead = sorted(t for t, v in ydata.items() if not v)
    if dead:
        print(f"WARNING: Yahoo returned nothing for: {dead} "
              f"(check the ticker mapping for these)")

    # --- Target price source priority: Google Finance > Yahoo > TradingView ---
    # 1) Google Finance (primary). Fetch for every underlying that resolves to a
    #    local ticker; overrides the Yahoo target where a value is found.
    gmap = {}
    for r in drs:
        key = (r["underlying"], r["underlyingExchange"])
        yt = ymap.get(key)
        if yt and yt not in gmap:
            local = local_ticker(r)
            if local:
                gmap[yt] = (local, r["underlyingExchange"])
    print(f"Fetching Google Finance targets + P/E / dividend yield for {len(gmap)} underlyings ...")
    gres = fetch_google(gmap)
    for yt, (gt, gccy, gpe, gdy) in gres.items():
        d = ydata.get(yt) or {}
        if gt:
            d["target"] = gt
            d["target_src"] = "Google Finance"
            if gccy and not d.get("currency"):
                d["currency"] = gccy
        # Metric fallback: fill P/E and dividend yield only where Yahoo has none.
        if d.get("pe") is None and gpe is not None:
            d["pe"] = gpe
        if d.get("divYield") is None and gdy is not None:
            d["divYield"] = gdy
        ydata[yt] = d
    n_google = sum(1 for v in gres.values() if v[0])
    print(f"  Google Finance targets: {n_google}")

    # 2) Yahoo Finance: label tickers that keep a Yahoo target (no Google value).
    for d in ydata.values():
        if d and d.get("target") and not d.get("target_src"):
            d["target_src"] = "Yahoo Finance"

    # 3) TradingView fallback: stock underlyings (not ETFs) with no target yet.
    seen_tv = set()
    for r in drs:
        key = (r["underlying"], r["underlyingExchange"])
        yt = ymap.get(key)
        if not yt or yt in seen_tv or r.get("underlyingClassId") == "7":
            continue
        seen_tv.add(yt)
        d = ydata.get(yt) or {}
        if d.get("target"):
            continue
        local = local_ticker(r)
        if not local:
            continue
        tv_target, tv_ccy = fetch_tv_target(r["underlyingExchange"], local)
        expected_ccy = d.get("currency") or EXCH[r["underlyingExchange"]][3]
        if tv_target and tv_ccy == expected_ccy:
            d["target"] = tv_target
            d["target_src"] = "TradingView"
            ydata[yt] = d
            print(f"  TradingView target for {yt}: {tv_target:,.2f} {tv_ccy}")

    # 4) TradingView metric fallback: P/E, P/BV, dividend yield still missing
    #    after Yahoo + Google. Ratios only, so no currency conversion needed.
    tv_map = {}
    for r in drs:
        yt = ymap.get((r["underlying"], r["underlyingExchange"]))
        if not yt or yt in tv_map:
            continue
        d = ydata.get(yt) or {}
        need = (d.get("pe") is None, d.get("pbv") is None, d.get("divYield") is None)
        if not any(need):
            continue
        local = local_ticker(r)
        if local:
            tv_map[yt] = (local, r["underlyingExchange"], need)
    if tv_map:
        print(f"Fetching TradingView P/E / P/BV / dividend yield fallback for "
              f"{len(tv_map)} underlyings ...")

        def _tvm(item):
            yt, (local, ex, need) = item
            return yt, fetch_tv_metrics(ex, local), need

        n_pe = n_pbv = n_dy = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(_tvm, it) for it in tv_map.items()]
            for f in as_completed(futures):
                yt, (tpe, tpbv, tdy), (need_pe, need_pbv, need_dy) = f.result()
                d = ydata.get(yt) or {}
                if need_pe and tpe is not None:
                    d["pe"] = tpe; n_pe += 1
                if need_pbv and tpbv is not None:
                    d["pbv"] = tpbv; n_pbv += 1
                if need_dy and tdy is not None:
                    d["divYield"] = tdy; n_dy += 1
                ydata[yt] = d
        print(f"  TradingView filled — P/E: {n_pe}, P/BV: {n_pbv}, Div yield: {n_dy}")

    ccys = sorted({v.get("currency") for v in ydata.values() if v.get("currency")} |
                  {c for (_, _, _, c) in EXCH.values()})
    fx = fetch_fx([("GBP" if c == "GBp" else c) for c in ccys])
    print("FX rates to THB:", {k: round(v, 6) for k, v in fx.items()})

    rows = build_rows(payload, ymap, ydata, fx)
    (HERE / "dashboard_data.json").write_text(
        json.dumps({"tradingDate": trading_date,
                    "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "fx": fx, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    out = write_excel(rows, trading_date, fx)
    n_target = sum(1 for r in rows if r["target_thb"] is not None)
    by_src = {}
    for r in rows:
        if r["target_src"]:
            by_src[r["target_src"]] = by_src.get(r["target_src"], 0) + 1
    src_txt = ", ".join(f"{k}: {v}" for k, v in sorted(by_src.items()))
    print(f"OK: {len(rows)} DRs, {n_target} with target price ({src_txt}) -> {out}")
