# -*- coding: utf-8 -*-
"""Render dashboard_data.json (from build_dr_dashboard.py) -> dr_dashboard.html.

Published to https://pridil.github.io/dr-dashboard/ (Netlify mirrors it): weekly by
deploy_dashboard.ps1, on weekdays by the cloud workflow (cloud/pages.yml).
"""
import base64, json, html, io, os, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
# DR_DASH_DATA points the renderer at another data folder (the weekday daily
# update, build_daily.py, renders from daily_draft\). Unset = the weekly build,
# exactly as before. Logos and company profiles always come from HERE.
DATA = Path(os.environ.get("DR_DASH_DATA") or HERE).resolve()
DAILY = os.environ.get("DR_DASH_MODE") == "daily"
SRC = DATA / "dashboard_data.json"
PROFILES_SRC = HERE / "profiles.json"
OUT = DATA / "dr_dashboard.html"

d = json.loads(SRC.read_text(encoding="utf-8"))
rows = d["rows"]

# Click-through detail behind each underlying name (fetch_profiles.py).
# Optional: a missing/broken cache degrades to a plain, non-clickable table.
try:
    _pf = json.loads(PROFILES_SRC.read_text(encoding="utf-8"))
    PROFILES = _pf.get("profiles", {})
    PROFILES_AT = _pf.get("generated", "")
except Exception:
    PROFILES, PROFILES_AT = {}, ""

# Only ship profiles for underlyings actually on the page.
_used = {r["yahoo"] for r in rows if r.get("yahoo")}
PROFILES_JSON = json.dumps(
    {k: v for k, v in PROFILES.items() if k in _used},
    ensure_ascii=False, separators=(",", ":"),
).replace("</", "<\\/")          # never let a summary close the <script> early
PROFILES_AT_JS = json.dumps(PROFILES_AT)

# YTD Return of each DR's UNDERLYING stock/ETF (fetch_ytd.py), e.g. NVDA for
# NVDA19: latest close this week / last close of the previous year - 1, in the
# underlying's own currency. User's rule 2026-10-07: underlying price, not the DR
# price, so DRs listed this year still get a figure. Only underlyings that
# themselves listed this year (2026 IPOs) have no base and show "-".
try:
    _yt = json.loads((DATA / "ytd.json").read_text(encoding="utf-8"))
    YTD, YTD_YEAR = _yt.get("ytd", {}), _yt.get("year")
    YTD_BASES = _yt.get("bases", {})
except Exception:
    YTD, YTD_YEAR, YTD_BASES = {}, None, {}


def ytd_return(r):
    return YTD.get(r.get("yahoo"))


def data_uri(fn):
    """Embed a local PNG as a base64 data URI so the page/PDF stays self-contained."""
    p = HERE / fn
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


# Yuanta Securities brand logo (blue for light backgrounds, white for dark).
LOGO_BLUE = data_uri("yuanta_logo.png")
LOGO_WHITE = data_uri("yuanta_logo_white.png")


def _fmt_range(a, b):
    """Human date range, e.g. '3-7 August 2026', '30 June - 4 July 2026'."""
    if a.year != b.year:
        return f"{a.day} {a:%B} {a.year} - {b.day} {b:%B} {b.year}"
    if a.month != b.month:
        return f"{a.day} {a:%B} - {b.day} {b:%B} {b.year}"
    return f"{a.day}-{b.day} {a:%B} {a.year}"


def use_week_label(data):
    """The business week the dashboard is USED in: Monday-Friday of the week
    AFTER the (Friday) close the data is built from. Built on a Saturday from
    Friday's close, it labels the coming Mon-Fri (e.g. Fri 31 Jul close ->
    '3-7 August 2026'). Anchored on the generation date, falling back to today."""
    ref = None
    for key in ("generated", "tradingDate"):
        try:
            ref = datetime.strptime(str(data.get(key, ""))[:10], "%Y-%m-%d").date()
            break
        except ValueError:
            continue
    if ref is None:
        ref = datetime.now().date()
    monday = ref - timedelta(days=ref.weekday()) + timedelta(days=7)  # next week's Monday
    return _fmt_range(monday, monday + timedelta(days=4))


# Since the dashboard updates every weekday (user 2026-11-06), the title shows the
# day it was updated, e.g. "6 November 2026", not a Mon-Fri week range.
_upd = datetime.strptime(str(d.get("generated", ""))[:10], "%Y-%m-%d").date()
WEEK = f"{_upd.day} {_upd:%B %Y}"
DAILY_NOTE = (
    f' · <b>daily update</b>: prices, P/E, P/BV, dividend yield &amp; YTD as of '
    f'{d["generated"]}; DR list &amp; analyst targets from the Saturday build '
    f'({d.get("base_generated", "")})'
) if DAILY else ""
# Contingency (user rule 2026-11-06): when set.or.th could not price the DRs,
# closing prices come from TradingView - say so on the page, never silently.
_ps = d.get("price_source") or ""
if DAILY and _ps and _ps != "set.or.th":
    _pc = d.get("price_counts") or {}
    DAILY_NOTE += (
        f' · <b style="color:var(--neg)">closing prices: '
        + ("from TradingView today &mdash; set.or.th was unavailable"
           if not _pc.get("set") else
           f'{_pc.get("tradingview", 0)} DR(s) from TradingView (set.or.th had no price for them)')
        + (f'; {_pc["kept"]} DR(s) not on TradingView keep their last available close'
           if _pc.get("kept") else '')
        + '</b>'
    )

# Target-price source -> short CSS class for the coloured tag.
SRC_CLS = {"Google Finance": "g", "Yahoo Finance": "y", "TradingView": "t"}

# ISO currency code -> full name, for the exchange-rate disclaimer.
CCY_NAME = {
    "USD": "US Dollar", "EUR": "Euro", "HKD": "Hong Kong Dollar",
    "JPY": "Japanese Yen", "CNY": "Chinese Yuan (Renminbi)",
    "SGD": "Singapore Dollar", "VND": "Vietnamese Dong",
    "TWD": "Taiwan Dollar", "DKK": "Danish Krone", "GBP": "Pound Sterling",
    "THB": "Thai Baht",
}

def fmt(v, dp=2):
    if v is None:
        return "-"
    return f"{v:,.{dp}f}"

def title_name(n):
    # Keep tickers/acronyms readable: title-case only long uppercase words
    words = []
    for w in n.split():
        if w.isupper() and len(w) > 3:
            words.append(w.title())
        else:
            words.append(w)
    return " ".join(words)

countries = []
for r in rows:
    if r["country"] not in countries:
        countries.append(r["country"])
flags = {r["country"]: r["flag"] for r in rows}

nav = "".join(
    f'<a class="chip" href="#c-{c.replace(" ", "-")}"><span class="fl">{flags[c]}</span>{c.title()}</a>'
    for c in countries
)

body_rows = []
cur = None
for r in rows:
    if r["country"] != cur:
        cur = r["country"]
        cid = cur.replace(" ", "-")
        n = sum(1 for x in rows if x["country"] == cur)
        body_rows.append(
            f'<tr class="group" id="c-{cid}" data-country="{html.escape(cur)}">'
            f'<td colspan="11"><span class="fl">{r["flag"]}</span>'
            f'<span class="gname">{html.escape(cur)}</span>'
            f'<span class="gcount">{n} DR</span></td></tr>'
        )
    up = r["upside"]
    upcls = "" if up is None else (" pos" if up >= 0 else " neg")
    ytd = ytd_return(r)
    ytdcls = "" if ytd is None else (" pos" if ytd >= 0 else " neg")
    src = r["target_src"]
    src_tag = (
        f'<span class="src src-{SRC_CLS.get(src, "na")}">{html.escape(src)}</span>'
        if src else '<span class="src src-na">—</span>'
    )
    disp = title_name(r["underlying"] or "-")
    yt = r.get("yahoo")
    # Clickable only where there is something to show; otherwise plain text,
    # so the page never offers a control that opens an empty panel.
    if yt and yt in PROFILES:
        name_cell = (
            f'<button type="button" class="uname" data-y="{html.escape(yt)}" '
            f'data-sym="{html.escape(r["symbol"])}" '
            f'data-etf="{"1" if r.get("is_etf") else "0"}">{html.escape(disp)}</button>'
        )
    else:
        name_cell = html.escape(disp)

    body_rows.append(
        f'<tr class="dr" data-country="{html.escape(r["country"])}" '
        f'data-s="{html.escape((r["symbol"] + " " + (r["underlying"] or "")).lower())}">'
        f'<td class="sym">{html.escape(r["symbol"])}</td>'
        f'<td class="name">{name_cell}</td>'
        f'<td class="num">{fmt(r["close"])}</td>'
        f'<td class="ratio">{html.escape(r["ratio"])}</td>'
        f'<td class="num">{fmt(r["target_thb"])}</td>'
        f'<td class="num{upcls}">{fmt(up)}</td>'
        f'<td class="num">{fmt(r["pe"])}</td>'
        f'<td class="num">{fmt(r["pbv"])}</td>'
        f'<td class="num">{fmt(r["divYield"])}</td>'
        f'<td class="num{ytdcls}">{fmt(ytd)}</td>'
        f'<td class="src-cell">{src_tag}</td>'
        f'</tr>'
    )

n_total = len(rows)
n_ytd = sum(1 for r in rows if ytd_return(r) is not None)
YTD_NOTE = (
    f"YTD Return: price change of the <b>underlying</b> stock/ETF (e.g. NVDA for "
    f"NVDA19) from its last close of {YTD_YEAR - 1} to its latest close this week, "
    f"in the underlying's own currency (excludes THB exchange-rate moves and "
    f"dividends; adjusted for splits), source Yahoo Finance; shown for "
    f"{n_ytd}/{n_total} DRs &mdash; &ldquo;-&rdquo; means the underlying itself "
    f"listed in {YTD_YEAR} and has no {YTD_YEAR - 1} close"
) if YTD_YEAR else "YTD Return: unavailable this run"
n_tgt = sum(1 for r in rows if r["target_thb"] is not None)
src_counts = {}
for r in rows:
    if r["target_src"]:
        src_counts[r["target_src"]] = src_counts.get(r["target_src"], 0) + 1
n_google = src_counts.get("Google Finance", 0)
n_yahoo = src_counts.get("Yahoo Finance", 0)
n_tv = src_counts.get("TradingView", 0)

fx = d.get("fx", {})
fx_items = "".join(
    f'<li><b>1 {c} = {format(v, ".6g")} THB</b>'
    f'<span class="cn">{CCY_NAME.get(c, c)}</span></li>'
    for c, v in sorted(fx.items()) if c != "THB"
)

page = f"""<meta charset="utf-8">
<title>SET DR Dashboard ({WEEK})</title>
<style>
:root {{
  --head1: #1E4E9E; --head2: #163C7E;
  --ink: #1C2635; --ink-soft: #5B6B82; --ink-faint: #8A97A8;
  --bg: #F4F6FA; --surface: #FFFFFF; --line: #DCE3EE;
  --group-bg: #EAF0F9; --group-ink: #1B3A6B;
  --hover: #F2F6FC; --chip-bg: #FFFFFF;
  --pos: #1A7F4B; --neg: #B3392E;
  --link: #1A56C4;
  --head-ink: #FFFFFF;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --head1: #1D3E7C; --head2: #142B58;
    --ink: #E4EAF4; --ink-soft: #9DABC0; --ink-faint: #6B7A90;
    --bg: #0E1420; --surface: #161D2B; --line: #2A3548;
    --group-bg: #1B2536; --group-ink: #A9C4EF;
    --hover: #1C2637; --chip-bg: #161D2B;
    --pos: #4CBF84; --neg: #E07B6F;
    --link: #8FB6F5;
  }}
}}
:root[data-theme="dark"] {{
  --head1: #1D3E7C; --head2: #142B58;
  --ink: #E4EAF4; --ink-soft: #9DABC0; --ink-faint: #6B7A90;
  --bg: #0E1420; --surface: #161D2B; --line: #2A3548;
  --group-bg: #1B2536; --group-ink: #A9C4EF;
  --hover: #1C2637; --chip-bg: #161D2B;
  --pos: #4CBF84; --neg: #E07B6F;
  --link: #8FB6F5;
}}
:root[data-theme="light"] {{
  --head1: #1E4E9E; --head2: #163C7E;
  --ink: #1C2635; --ink-soft: #5B6B82; --ink-faint: #8A97A8;
  --bg: #F4F6FA; --surface: #FFFFFF; --line: #DCE3EE;
  --group-bg: #EAF0F9; --group-ink: #1B3A6B;
  --hover: #F2F6FC; --chip-bg: #FFFFFF;
  --pos: #1A7F4B; --neg: #B3392E;
  --link: #1A56C4;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.45 "Segoe UI", Arial, "Helvetica Neue", sans-serif;
}}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 64px; }}
header.top h1 {{ font-size: 22px; font-weight: 700; margin: 0 0 4px; letter-spacing: .2px; }}
header.top h1 .wk {{ font-weight: 600; color: var(--head1); font-size: 19px; }}
header.top .meta {{ color: var(--ink-soft); font-size: 12.5px; margin: 0 0 4px; }}
header.top .note {{ color: var(--ink-faint); font-size: 11.5px; margin: 0; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 18px 0 14px; }}
.chip {{
  display: inline-flex; align-items: center; gap: 6px;
  background: var(--chip-bg); border: 1px solid var(--line); border-radius: 4px;
  color: var(--ink); text-decoration: none; font-size: 12px; padding: 4px 10px;
  white-space: nowrap;
}}
.chip:hover {{ border-color: var(--head1); color: var(--head1); }}
.chip .fl {{ font-size: 13px; }}
#q {{
  margin-left: auto; min-width: 230px; padding: 6px 10px;
  border: 1px solid var(--line); border-radius: 4px;
  background: var(--surface); color: var(--ink); font: inherit; font-size: 13px;
}}
#q:focus {{ outline: 2px solid var(--head1); outline-offset: -1px; }}
.tablebox {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  overflow-x: auto; box-shadow: 0 1px 3px rgba(16,32,64,.06);
}}
table {{ border-collapse: collapse; width: 100%; min-width: 1060px; font-size: 16px; }}
thead th {{
  position: sticky; top: 0; z-index: 2;
  background: linear-gradient(180deg, var(--head1), var(--head2));
  color: var(--head-ink); font-size: 12.5px; font-weight: 600;
  padding: 11px 10px; text-align: center; letter-spacing: .2px;
  border-right: 1px solid rgba(255,255,255,.14);
}}
thead th:last-child {{ border-right: none; }}
thead th .hint {{ font-weight: 400; font-size: 10.5px; opacity: .8; }}
tbody td {{
  padding: 8px 10px; border-bottom: 1px solid var(--line);
  border-right: 1px solid var(--line); text-align: center;
  font-variant-numeric: tabular-nums;
}}
tbody td:last-child {{ border-right: none; }}
tr.dr:hover td {{ background: var(--hover); }}
td.sym {{ font-weight: 700; white-space: nowrap; }}
td.name {{ text-align: left; }}
td.ratio {{ white-space: nowrap; color: var(--ink-soft); }}
td.num, td.ratio {{ font-size: 17.5px; }}
td.num.pos {{ color: var(--pos); }}
td.num.neg {{ color: var(--neg); }}
td.src-cell {{ white-space: nowrap; }}
td.src-cell .src {{ font-size: 9px; padding: 1px 6px; letter-spacing: .1px; }}
.src {{
  display: inline-block; font-size: 10.5px; font-weight: 600;
  padding: 2px 7px; border-radius: 999px; letter-spacing: .2px;
  border: 1px solid transparent; line-height: 1.4;
}}
.src-g {{ background: #E7F0FE; color: #1A56C4; border-color: #C4D9FA; }}
.src-y {{ background: #F3ECFB; color: #7A3EAE; border-color: #E1CDF3; }}
.src-t {{ background: #E9F6EF; color: #16794A; border-color: #C7E8D6; }}
.src-na {{ background: transparent; color: var(--ink-faint); border-color: transparent; }}
@media (prefers-color-scheme: dark) {{
  .src-g {{ background: #17294A; color: #8FB6F5; border-color: #274372; }}
  .src-y {{ background: #2A1F3D; color: #C6A2E8; border-color: #43315E; }}
  .src-t {{ background: #16301F; color: #74CFA0; border-color: #244C36; }}
}}
:root[data-theme="dark"] .src-g {{ background: #17294A; color: #8FB6F5; border-color: #274372; }}
:root[data-theme="dark"] .src-y {{ background: #2A1F3D; color: #C6A2E8; border-color: #43315E; }}
:root[data-theme="dark"] .src-t {{ background: #16301F; color: #74CFA0; border-color: #244C36; }}
:root[data-theme="light"] .src-g {{ background: #E7F0FE; color: #1A56C4; border-color: #C4D9FA; }}
:root[data-theme="light"] .src-y {{ background: #F3ECFB; color: #7A3EAE; border-color: #E1CDF3; }}
:root[data-theme="light"] .src-t {{ background: #E9F6EF; color: #16794A; border-color: #C7E8D6; }}
tr.group td {{
  background: var(--group-bg); color: var(--group-ink);
  font-weight: 700; text-align: left; font-size: 13px;
  padding: 9px 12px; letter-spacing: .4px;
}}
tr.group .fl {{ margin-right: 8px; }}
tr.group .gcount {{
  float: right; font-weight: 500; font-size: 11.5px; color: var(--ink-soft); letter-spacing: 0;
}}
#none {{ display: none; color: var(--ink-soft); padding: 26px; text-align: center; }}
.disc {{ margin-top: 18px; display: grid; gap: 14px; }}
.disc .card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  padding: 14px 16px 16px;
}}
.disc h3 {{ margin: 0 0 8px; font-size: 13.5px; font-weight: 700; color: var(--ink); }}
.disc p {{ font-size: 12px; color: var(--ink-soft); line-height: 1.55; margin: 0 0 8px; }}
.disc p:last-child {{ margin-bottom: 0; }}
.disc b {{ color: var(--ink); font-weight: 600; }}
.disc ul.srclist {{ margin: 0 0 8px; padding: 0; list-style: none; display: grid; gap: 7px; }}
.disc ul.srclist li {{ font-size: 12px; color: var(--ink-soft); line-height: 1.5; }}
.disc ul.srclist .src {{ margin-right: 6px; }}
.disc ul.fx {{
  margin: 4px 0 8px; padding: 0; list-style: none;
  display: flex; flex-wrap: wrap; gap: 8px 10px;
}}
.disc ul.fx li {{
  font-size: 11.5px; background: var(--group-bg); border: 1px solid var(--line);
  border-radius: 4px; padding: 5px 10px; display: flex; gap: 8px; align-items: baseline;
}}
.disc ul.fx b {{ font-variant-numeric: tabular-nums; }}
.disc ul.fx .cn {{ color: var(--ink-faint); font-size: 11px; }}
footer {{ color: var(--ink-faint); font-size: 11.5px; margin-top: 14px; }}
/* Yuanta brand logo, top-right */
header.top {{ position: relative; padding-right: 190px; }}
.brand {{ position: absolute; top: 2px; right: 0; height: 34px; width: auto; }}
.brand-dark {{ display: none; }}
@media (prefers-color-scheme: dark) {{ .brand-light {{ display: none; }} .brand-dark {{ display: inline; }} }}
:root[data-theme="dark"] .brand-light {{ display: none; }}
:root[data-theme="dark"] .brand-dark {{ display: inline; }}
:root[data-theme="light"] .brand-light {{ display: inline; }}
:root[data-theme="light"] .brand-dark {{ display: none; }}
/* action buttons (print / download pdf) */
.btn {{
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  background: var(--head1); color: #fff; border: 1px solid var(--head1);
  border-radius: 4px; font: inherit; font-size: 12px; padding: 6px 11px;
  text-decoration: none; white-space: nowrap;
}}
.btn:hover {{ background: var(--head2); border-color: var(--head2); }}
.btn-dl {{ background: var(--surface); color: var(--head1); }}
.btn-dl:hover {{ background: var(--group-bg); color: var(--head1); }}
/* Underlying-asset click-through: company overview (stocks) / holdings (ETFs).
   Kept as ink-coloured text with a dotted rule rather than 500 blue links, which
   would turn the widest column into a wall of colour. */
.uname {{
  font: inherit; color: inherit; background: none; border: 0; padding: 0;
  text-align: left; cursor: pointer; border-bottom: 1px dotted var(--ink-faint);
}}
.uname:hover {{ color: var(--link); border-bottom-color: var(--link); }}
.uname:focus-visible {{ outline: 2px solid var(--link); outline-offset: 2px; }}
#pf {{
  width: min(680px, calc(100vw - 32px)); max-height: min(82vh, 760px);
  padding: 0; border: 1px solid var(--line); border-radius: 8px;
  background: var(--surface); color: var(--ink);
  box-shadow: 0 18px 50px rgba(8,16,32,.30);
}}
#pf::backdrop {{ background: rgba(9,16,30,.55); }}
.pf-head {{
  position: relative;
  background: linear-gradient(180deg, var(--head1), var(--head2));
  color: var(--head-ink); padding: 14px 48px 13px 18px;
}}
.pf-head h2 {{ margin: 0; font-size: 16px; font-weight: 700; line-height: 1.3; }}
.pf-sub {{ margin: 3px 0 0; font-size: 12px; opacity: .85; }}
.pf-x {{
  position: absolute; top: 11px; right: 11px; width: 27px; height: 27px;
  display: flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,.15); color: #fff; border: 0; border-radius: 4px;
  font-size: 15px; line-height: 1; cursor: pointer;
}}
.pf-x:hover {{ background: rgba(255,255,255,.3); }}
.pf-body {{ padding: 16px 18px 18px; overflow-y: auto; max-height: calc(min(82vh, 760px) - 68px); }}
.pf-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 4px; }}
.pf-tag {{
  font-size: 11px; padding: 3px 9px; border-radius: 999px;
  background: var(--group-bg); color: var(--group-ink); border: 1px solid var(--line);
}}
.pf-tag b {{ font-weight: 700; }}
.pf-h3 {{
  margin: 16px 0 8px; font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: .5px; color: var(--ink-faint);
}}
.pf-sum {{ margin: 0; font-size: 13px; line-height: 1.62; color: var(--ink-soft); }}
.pf-note {{ margin: 0; font-size: 12.5px; line-height: 1.55; color: var(--ink-soft); }}
.pf-meta {{ margin: 13px 0 0; display: grid; gap: 5px; font-size: 12px; color: var(--ink-soft); }}
.pf-meta b {{ color: var(--ink); font-weight: 600; }}
.pf-meta a {{ color: var(--link); word-break: break-all; }}
/* min-width:0 undoes the 1060px min-width the page table carries */
table.pf-hold {{ width: 100%; min-width: 0; border-collapse: collapse; }}
table.pf-hold td {{
  border: 0; border-bottom: 1px solid var(--line); padding: 7px 6px;
  font-size: 12.5px; text-align: left;
}}
table.pf-hold tr:last-child td {{ border-bottom: 0; }}
table.pf-hold .r {{ color: var(--ink-faint); width: 20px; font-variant-numeric: tabular-nums; }}
table.pf-hold .hs {{ font-weight: 700; white-space: nowrap; }}
table.pf-hold .hn {{ color: var(--ink-soft); }}
table.pf-hold .hw {{
  text-align: right; white-space: nowrap; width: 56px;
  font-variant-numeric: tabular-nums; font-weight: 600;
}}
table.pf-hold .pf-bar {{ width: 86px; }}
table.pf-hold .pf-bar i {{ display: block; height: 7px; border-radius: 3px; background: var(--link); }}
.pf-foot {{
  margin: 16px 0 0; padding-top: 11px; border-top: 1px solid var(--line);
  font-size: 11px; color: var(--ink-faint); line-height: 1.5;
}}
@media (max-width: 700px) {{ #q {{ margin-left: 0; width: 100%; }} header.top {{ padding-right: 0; }} .brand {{ position: static; display: block; margin-bottom: 10px; }} }}
/* Print / Save-as-PDF layout */
@media print {{
  @page {{ size: A4 landscape; margin: 8mm; }}
  body {{ background: #fff; color: #000; }}
  .wrap {{ max-width: none; padding: 0; }}
  .controls, #q, .chip, .btn, #none {{ display: none !important; }}
  header.top {{ padding-right: 170px; }}
  .brand-light {{ display: inline !important; }} .brand-dark {{ display: none !important; }}
  header.top .note, header.top .meta {{ color: #333 !important; }}
  header.top h1 {{ color: #14315f !important; }}
  .tablebox {{ border: none; box-shadow: none; overflow: visible; }}
  table {{ min-width: 0; width: 100%; font-size: 8.3px; }}
  thead {{ display: table-header-group; }}
  thead th {{
    position: static; background: #1E4E9E !important; color: #fff !important;
    padding: 6px 5px; -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  tbody td {{ padding: 3.5px 5px; }}
  td.num, td.ratio {{ font-size: 9.2px; }}
  td.src-cell .src {{ font-size: 6.8px; padding: 0 4px; }}
  tr, td, th {{ page-break-inside: avoid; }}
  tr.group td {{
    background: #EAF0F9 !important; color: #1B3A6B !important;
    -webkit-print-color-adjust: exact; print-color-adjust: exact;
  }}
  td.num.pos {{ color: #1A7F4B !important; }} td.num.neg {{ color: #B3392E !important; }}
  .src {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .disc {{ margin-top: 12px; }} .disc .card {{ break-inside: avoid; }}
  .disc p, .disc li, footer {{ font-size: 8px; }}
  a[href]::after {{ content: ""; }}
  /* the detail panel is an on-screen affordance only */
  #pf, thead th .hint {{ display: none !important; }}
  .uname {{ border-bottom: 0; color: #000 !important; }}
}}
</style>
<div class="wrap">
  <header class="top">
    <img class="brand brand-light" src="{LOGO_BLUE}" alt="Yuanta Securities">
    <img class="brand brand-dark" src="{LOGO_WHITE}" alt="Yuanta Securities">
    <h1>SET DR Dashboard <span class="wk">({WEEK})</span></h1>
    <p class="meta">{n_total} Depositary Receipts · {len(countries)} markets · trading date {d["tradingDate"]} · generated {d["generated"]}{DAILY_NOTE}</p>
    <p class="note">Target Price (THB) = analyst consensus target (local currency) × FX to THB ÷ conversion ratio · Upside vs DR close on SET</p>
    <p class="note">Target source priority: <span class="src src-g">Google Finance</span> → <span class="src src-y">Yahoo Finance</span> → <span class="src src-t">TradingView</span> · this run: {n_google} Google · {n_yahoo} Yahoo · {n_tv} TradingView</p>
  </header>
  <div class="controls">
    {nav}
    <input id="q" type="search" placeholder="Search DR or company…" aria-label="Search DR">
    <button class="btn" onclick="window.print()" title="Print or save the dashboard as a PDF">🖨 Save as PDF / Print</button>
    <a class="btn btn-dl" href="dr_dashboard.pdf" download title="Download the pre-rendered PDF">⬇ Download PDF</a>
    <a class="btn btn-dl" href="dr_dashboard.xlsx" download title="Download this table as an Excel workbook">⬇ Download Excel</a>
  </div>
  <div class="tablebox">
    <table>
      <thead><tr>
        <th>DR</th><th>Underlying Asset<br><span class="hint">click a name for details</span></th><th>Closing Price<br>(THB)</th><th>Conversion<br>Ratio</th>
        <th>Target Price<br>(THB)</th><th>Upside<br>(%)</th><th>P/E</th><th>P/BV</th><th>Dividend Yield<br>(%)</th><th>YTD Return<br>(%)</th><th>Source</th>
      </tr></thead>
      <tbody>
        {"".join(body_rows)}
      </tbody>
    </table>
    <div id="none">No DR matches your search.</div>
  </div>
  <section class="disc">
    <div class="card">
      <h3>Analyst target price — sources &amp; methodology</h3>
      <p>The <b>Target Price (THB)</b> shown for each DR is derived from a published
         analyst <b>consensus</b> target for the underlying security, converted to Thai Baht.
         Sources are used in the following priority order (this run:
         {n_google} Google · {n_yahoo} Yahoo · {n_tv} TradingView):</p>
      <ul class="srclist">
        <li><span class="src src-g">Google Finance</span> — the <b>Average</b> of Wall Street
            analysts' 12-month price targets shown on google.com/finance. Published mainly for
            US-listed stocks; used first where available.</li>
        <li><span class="src src-y">Yahoo Finance</span> — the mean analyst 12-month price target
            (<i>targetMeanPrice</i>) across the analysts covering the stock. Covers most
            international names.</li>
        <li><span class="src src-t">TradingView</span> — the 1-year analyst consensus target
            (<i>price target</i>). Used only where the two sources above have none
            (e.g. China A-shares).</li>
      </ul>
      <p><b>Brokerage note:</b> each figure is an <b>aggregated consensus of price targets from
         multiple sell-side brokerages / research houses</b> covering the stock. Google Finance,
         Yahoo Finance and TradingView publish only the aggregate (and, where shown, the number of
         contributing analysts) — they <b>do not disclose the individual brokerages</b> behind each
         target, so specific broker names cannot be attributed here. ETFs and newly listed DRs
         typically have no analyst coverage and therefore no target. Targets are analysts' opinions,
         not guarantees, and change frequently.</p>
    </div>
    <div class="card">
      <h3>Exchange rates used in the calculation</h3>
      <p>Target Price (THB) = local-currency analyst target × exchange rate to THB ÷ conversion ratio.
         The exchange rates below are <b>indicative spot mid-market rates</b> retrieved from
         Yahoo Finance at the dashboard generation time ({d["generated"]}) and are used
         <b>solely</b> to express foreign-currency targets in Thai Baht:</p>
      <ul class="fx">{fx_items}</ul>
      <p>These rates <b>fluctuate continuously</b> and may differ from the rates actually available
         for currency conversion, from the DR issuer's conversion / settlement rate, and from
         interbank or retail rates at any given moment. They are indicative only and are not a quote
         or an offer to transact at these levels.</p>
    </div>
  </section>
  <dialog id="pf" aria-labelledby="pf-title">
    <div class="pf-head">
      <h2 id="pf-title"></h2>
      <p class="pf-sub" id="pf-sub"></p>
      <button type="button" class="pf-x" id="pf-x" aria-label="Close details">&#10005;</button>
    </div>
    <div class="pf-body" id="pf-body"></div>
  </dialog>
  <footer>Prices &amp; conversion ratios: set.or.th · Analyst target price sourced in priority order Google Finance → Yahoo Finance → TradingView (Google publishes 12-month targets for US listings; Yahoo covers most international names; TradingView 1-year consensus catches China A-shares) · P/E, P/BV &amp; dividend yield: Yahoo Finance, falling back to Google Finance / TradingView where Yahoo has none · Dividend yield is annualized (per year, trailing/forward 12 months) &middot; {YTD_NOTE} &middot; Company overviews, sector classification &amp; ETF holdings (shown when an underlying asset name is clicked): Yahoo Finance, with China A-share ETF holdings from Eastmoney (quarterly disclosure) and Vietnamese ETF holdings from the fund managers / TradingView &mdash; each panel states its own source and as-of date · {n_tgt}/{n_total} DRs have analyst targets — {n_google} Google, {n_yahoo} Yahoo, {n_tv} TradingView (ETFs and newly listed names typically have none) · Not investment advice.</footer>
</div>
<script>
const q = document.getElementById('q');
const drs = [...document.querySelectorAll('tr.dr')];
const groups = [...document.querySelectorAll('tr.group')];
const none = document.getElementById('none');
q.addEventListener('input', () => {{
  const s = q.value.trim().toLowerCase();
  let any = false;
  const visByCountry = {{}};
  for (const tr of drs) {{
    const show = !s || tr.dataset.s.includes(s);
    tr.style.display = show ? '' : 'none';
    if (show) {{ any = true; visByCountry[tr.dataset.country] = true; }}
  }}
  for (const g of groups) g.style.display = visByCountry[g.dataset.country] ? '' : (s ? 'none' : '');
  none.style.display = any ? 'none' : 'block';
}});

/* ---- Underlying-asset detail panel: overview for stocks, holdings for ETFs ----
   Content is built with DOM APIs rather than innerHTML: every string here comes
   from a third-party feed and must never be parsed as markup. */
const PROFILES = {PROFILES_JSON};
const PROFILES_AT = {PROFILES_AT_JS};
const pf = document.getElementById('pf');
const pfTitle = document.getElementById('pf-title');
const pfSub = document.getElementById('pf-sub');
const pfBody = document.getElementById('pf-body');

const el = (t, c, x) => {{
  const e = document.createElement(t);
  if (c) e.className = c;
  if (x != null) e.textContent = x;
  return e;
}};
const tag = t => el('span', 'pf-tag', t);

function kv(k, v) {{
  const s = el('span');
  s.append(el('b', null, k + ': '), document.createTextNode(v));
  return s;
}}

/* items: [{{sym?, name, pct}}] - shared by top holdings and the sector split,
   scaled against the largest weight so the bars read as a ranking. */
function weightRows(items) {{
  const t = el('table', 'pf-hold'), tb = el('tbody');
  const max = Math.max(...items.map(o => o.pct || 0));
  items.forEach((o, i) => {{
    const tr = el('tr');
    tr.append(el('td', 'r', String(i + 1)));
    if (o.sym) tr.append(el('td', 'hs', o.sym));
    const name = el('td', 'hn', o.name);
    if (!o.sym) name.colSpan = 2;
    tr.append(name);
    const bar = el('td', 'pf-bar'), fill = el('i');
    fill.style.width = max > 0 ? Math.max((o.pct || 0) / max * 100, 3) + '%' : '0';
    bar.append(fill);
    tr.append(bar, el('td', 'hw', o.pct == null ? '–' : o.pct.toFixed(2) + '%'));
    tb.append(tr);
  }});
  t.append(tb);
  return t;
}}

function openProfile(btn) {{
  const p = PROFILES[btn.dataset.y];
  if (!p) return;
  const isEtf = btn.dataset.etf === '1';
  pfTitle.textContent = btn.textContent;
  pfSub.textContent = btn.dataset.sym + ' · ' + btn.dataset.y + ' · ' + (isEtf ? 'ETF' : 'Stock');
  pfBody.replaceChildren();

  const tags = el('div', 'pf-tags');
  if (isEtf) {{
    if (p.category) tags.append(tag('Category: ' + p.category));
    if (p.family) tags.append(tag('Provider: ' + p.family));
  }} else {{
    if (p.sector) tags.append(tag('Sector: ' + p.sector));
    if (p.industry) tags.append(tag('Industry: ' + p.industry));
  }}
  if (tags.children.length) pfBody.append(tags);

  if (isEtf) {{
    const hs = p.holdings || [];
    pfBody.append(el('div', 'pf-h3', hs.length ? 'Top ' + hs.length + ' holdings' : 'Top holdings'));
    if (hs.length) {{
      pfBody.append(weightRows(hs.map(h => ({{ sym: h[0], name: h[1], pct: h[2] }}))));
    }} else {{
      pfBody.append(el('p', 'pf-note',
        'Yahoo Finance publishes no holdings breakdown for this fund. That is expected for ' +
        'China A-share and Vietnamese ETFs, and for physically-backed commodity funds, which ' +
        'hold bullion rather than listed securities.'));
    }}
    if (p.sectors && p.sectors.length) {{
      pfBody.append(el('div', 'pf-h3', 'Sector breakdown'));
      pfBody.append(weightRows(p.sectors.map(s => ({{ name: s[0], pct: s[1] }}))));
    }}
    if (p.summary) {{
      pfBody.append(el('div', 'pf-h3', 'About this fund'), el('p', 'pf-sum', p.summary));
    }}
  }} else {{
    pfBody.append(el('div', 'pf-h3', 'Company overview'));
    pfBody.append(el('p', 'pf-sum',
      p.summary || 'No business description is published for this company.'));
    const meta = el('div', 'pf-meta');
    if (p.hq) meta.append(kv('Headquarters', p.hq));
    if (p.employees) meta.append(kv('Employees', p.employees.toLocaleString('en-US')));
    if (p.website) {{
      const s = el('span'), a = el('a', null, p.website);
      a.href = p.website; a.target = '_blank'; a.rel = 'noopener noreferrer';
      s.append(el('b', null, 'Website: '), a);
      meta.append(s);
    }}
    if (meta.children.length) pfBody.append(meta);
  }}

  /* Most funds come from Yahoo, but the China A-share and Vietnamese ETFs are
     sourced elsewhere and disclose on their own cadence - credit each fund's
     actual source and as-of date so a quarterly figure is never mistaken for
     a current one. */
  const src = p.src || 'Yahoo Finance';
  const stamp = p.asof
    ? ' · holdings as at ' + p.asof + (p.cadence ? ' (' + p.cadence + ')' : '')
    : (PROFILES_AT ? ' · retrieved ' + PROFILES_AT : '');
  pfBody.append(el('p', 'pf-foot',
    (isEtf ? 'Fund profile & holdings' : 'Company profile & sector classification') +
    ': ' + src + stamp +
    (isEtf ? ' · holdings change as the fund rebalances' : '') +
    ' · for information only, not investment advice.'));
  pfBody.scrollTop = 0;
  pf.showModal();
}}

document.querySelector('.tablebox tbody').addEventListener('click', e => {{
  const btn = e.target.closest('.uname');
  if (btn) openProfile(btn);
}});
document.getElementById('pf-x').addEventListener('click', () => pf.close());
/* a click that lands on the dialog element itself is a click on its backdrop */
pf.addEventListener('click', e => {{ if (e.target === pf) pf.close(); }});
</script>
"""
OUT.write_text(page, encoding="utf-8")
print("wrote", OUT, len(page), "chars")
