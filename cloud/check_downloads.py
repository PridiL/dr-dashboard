# -*- coding: utf-8 -*-
"""Refuse to publish unless the Download PDF / Download Excel files are the same build as the page.

    python check_downloads.py <site dir>     (index.html, dr_dashboard.pdf, dr_dashboard.xlsx)

User rule 2026-11-06: the two download buttons must always carry the data shown
on the dashboard. Each file states when it was built ("generated YYYY-MM-DD HH:MM"),
so all three stamps must be equal, and the page's links must carry that build's
?v= tag (so a browser or CDN cache can never hand out the previous file).
A mismatch - e.g. the PDF step failed and an older PDF was left beside a new page -
exits 1, the workflow stops before deploying, and the live site keeps its last
consistent build.
"""
import re
import sys
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

STAMP = r"generated\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"


def norm(s):
    return " ".join(s.split()) if s else None


site = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
html = (site / "index.html").read_text(encoding="utf-8")
m = re.search(STAMP, html)
page = norm(m.group(1)) if m else None

pdf = None
if (site / "dr_dashboard.pdf").exists():
    m = re.search(STAMP, PdfReader(str(site / "dr_dashboard.pdf")).pages[0].extract_text() or "")
    pdf = norm(m.group(1)) if m else None

xlsx = None
if (site / "dr_dashboard.xlsx").exists():
    a2 = load_workbook(site / "dr_dashboard.xlsx", read_only=True).active["A2"].value or ""
    m = re.search(STAMP, a2)
    xlsx = norm(m.group(1)) if m else None

tag = re.sub(r"\D", "", page or "")
links = {f: re.search(rf'href="{re.escape(f)}\?v=(\d+)"', html) for f in ("dr_dashboard.pdf", "dr_dashboard.xlsx")}
link_ok = all(l and l.group(1) == tag for l in links.values())

print(f"page {page} | PDF {pdf} | Excel {xlsx} | download links ?v={tag if link_ok else 'MISSING/STALE'}")
if page and page == pdf == xlsx and link_ok:
    print("OK: Download PDF and Download Excel are the same build as the page.")
    sys.exit(0)
print("::error::Download PDF / Excel do not match the page - not publishing.")
sys.exit(1)
