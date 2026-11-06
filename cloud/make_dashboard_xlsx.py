# -*- coding: utf-8 -*-
"""Excel version of the dashboard table -> dr_dashboard.xlsx ("Download Excel" button).

Built from the same dashboard_data.json + ytd.json the page is rendered from, so
the workbook always matches the page beside it: the Saturday full build, or the
weekday daily build when DR_DASH_DATA points at daily_build\\ (run_daily.ps1).

Columns follow the page (user-set order, 2026-10-07), with Country added first so
the sheet can be filtered. Percent columns hold the same numbers the page shows
(38.09 = 38.09 %), not Excel fractions. Separate from the weekly
DR_Dashboard_YYYY-MM-DD.xlsx archive, which build_dr_dashboard.py still writes.
"""
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DR_DASH_DATA") or HERE).resolve()
OUT = DATA / "dr_dashboard.xlsx"

d = json.loads((DATA / "dashboard_data.json").read_text(encoding="utf-8"))
try:
    ytd = json.loads((DATA / "ytd.json").read_text(encoding="utf-8")).get("ytd", {})
except Exception:
    ytd = {}

COLS = [  # header, width, row -> value, number format
    ("Country", 15, lambda r: r["country"].title(), None),
    ("DR", 13, lambda r: r["symbol"], None),
    ("Underlying Asset", 44, lambda r: r["underlying"], None),
    ("Closing Price (THB)", 13, lambda r: r.get("close"), "#,##0.00"),
    ("Conversion Ratio", 13, lambda r: r.get("ratio"), None),
    ("Target Price (THB)", 13, lambda r: r.get("target_thb"), "#,##0.00"),
    ("Upside (%)", 11, lambda r: r.get("upside"), "[Color10]#,##0.00;[Red]-#,##0.00"),
    ("P/E", 10, lambda r: r.get("pe"), "#,##0.00"),
    ("P/BV", 10, lambda r: r.get("pbv"), "#,##0.00"),
    ("Dividend Yield (%)", 12, lambda r: r.get("divYield"), "#,##0.00"),
    ("YTD Return (%)", 12, lambda r: ytd.get(r.get("yahoo")), "[Color10]#,##0.00;[Red]-#,##0.00"),
    ("Source", 15, lambda r: r.get("target_src"), None),
]

wb = Workbook()
ws = wb.active
ws.title = "DR Dashboard"
f = lambda **k: Font(name="Arial", **k)
upd = datetime.strptime(d["generated"][:10], "%Y-%m-%d")

ws["A1"] = f"SET DR Dashboard ({upd.day} {upd:%B %Y})"
ws["A1"].font = f(size=14, bold=True, color="1E4E9E")
info = f"{len(d['rows'])} Depositary Receipts · trading date {d['tradingDate']} · generated {d['generated']}"
if d.get("mode") == "daily":
    info += (f" · daily update: prices, P/E, P/BV, dividend yield & YTD; DR list & "
             f"analyst targets from the Saturday build ({d.get('base_generated', '')})")
ws["A2"] = info
ws["A2"].font = f(size=9, color="5B6B82")

HR = 4
head_fill = PatternFill("solid", fgColor="1E4E9E")
thin = Side(style="thin", color="DCE3EE")
for i, (name, width, _, _) in enumerate(COLS, 1):
    c = ws.cell(row=HR, column=i, value=name)
    c.font = f(bold=True, color="FFFFFF", size=10)
    c.fill = head_fill
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.column_dimensions[get_column_letter(i)].width = width
ws.row_dimensions[HR].height = 30

for n, r in enumerate(d["rows"], HR + 1):
    for i, (_, _, get, fmt) in enumerate(COLS, 1):
        v = get(r)
        c = ws.cell(row=n, column=i, value=v if v is not None else None)
        c.font = f(size=10, bold=(i == 2))
        c.border = Border(bottom=thin)
        if fmt and isinstance(v, (int, float)):
            c.number_format = fmt
            c.alignment = Alignment(horizontal="right")
        elif i in (2, 5, 12):
            c.alignment = Alignment(horizontal="center")

last = HR + len(d["rows"])
ws.auto_filter.ref = f"A{HR}:{get_column_letter(len(COLS))}{last}"
ws.freeze_panes = ws.cell(row=HR + 1, column=3)

notes = [
    "Closing Price & conversion ratio: set.or.th. Target Price (THB) = analyst consensus target "
    "(local currency) × FX to THB ÷ conversion ratio; Upside = Target / Closing Price − 1.",
    "YTD Return: the UNDERLYING stock/ETF's price change from its last close of the previous year, "
    "in its own currency (excludes FX and dividends). Blank = underlying listed this year.",
    "P/E, P/BV & dividend yield: Yahoo Finance, falling back to Google Finance / TradingView. "
    "Blank = not available. Not investment advice.",
]
for k, t in enumerate(notes, last + 2):
    ws.cell(row=k, column=1, value=t).font = f(size=8, italic=True, color="8A97A8")

wb.save(OUT)
print(f"wrote {OUT.name}: {len(d['rows'])} DRs, YTD for "
      f"{sum(1 for r in d['rows'] if ytd.get(r.get('yahoo')) is not None)}")
