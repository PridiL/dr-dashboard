# -*- coding: utf-8 -*-
"""Pre-render the DR dashboard to a downloadable PDF (dr_dashboard.pdf).

Loads the generated dr_dashboard.html in headless Chromium and prints it to PDF
using the page's own PRINT stylesheet (the same @media print rules that the
in-browser "Save as PDF" button uses), so the file and the button match:
landscape, controls hidden, blue column header repeated on every page, rows not
split across page breaks, white background.

Run AFTER make_dashboard_html.py. deploy_dashboard.ps1 publishes the PDF to
https://pridil.github.io/dr-dashboard/dr_dashboard.pdf (the page links to it).
"""
import io, os, sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = Path(__file__).resolve().parent
# DR_DASH_DATA = the weekday daily build folder (run_daily.ps1); unset = weekly.
DATA = Path(os.environ.get("DR_DASH_DATA") or HERE).resolve()
SRC = DATA / "dr_dashboard.html"
OUT = DATA / "dr_dashboard.pdf"

if not SRC.exists():
    raise SystemExit(f"{SRC} not found — run make_dashboard_html.py first.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(SRC.as_uri(), wait_until="networkidle", timeout=60000)
    page.emulate_media(media="print")          # honour @media print rules
    page.pdf(
        path=str(OUT),
        format="A4",
        landscape=True,
        print_background=True,
        prefer_css_page_size=True,              # let @page size/margins win if set
        margin={"top": "8mm", "bottom": "12mm", "left": "8mm", "right": "8mm"},
        display_header_footer=True,
        header_template="<span></span>",        # no header band
        footer_template=(
            '<div style="width:100%;font-size:8px;color:#8A97A8;'
            'padding:0 10mm;font-family:Arial,sans-serif;display:flex;'
            'justify-content:space-between;">'
            '<span>SET DR Dashboard — pridil.github.io/dr-dashboard</span>'
            '<span>Page <span class="pageNumber"></span> of '
            '<span class="totalPages"></span></span></div>'),
    )
    browser.close()

size_kb = OUT.stat().st_size / 1024
print(f"wrote {OUT.name} ({size_kb:,.0f} KB) at {datetime.now():%Y-%m-%d %H:%M}")
