"""Join the DR dashboard and the DR RRG dashboard into one Netlify site.

    python netlify_nav.py <stage dir> [<dr_rrg_combined.html>] [--rrg-href URL]

--rrg-href points the "DR RRG" tab somewhere other than rrg.html beside the page:
deploy_dashboard.ps1 uses it for the GitHub Pages copy of the DR dashboard, whose
RRG page lives in another repo (pridil.github.io/dr-rrg-live/rrg.html).

Run by deploy_netlify.ps1 after it has staged index.html (the DR dashboard).
Writes rrg.html (the DR RRG dashboard) beside it and puts the same tab bar,
"DR Dashboard | DR RRG", at the top of both, so the site reads as one dashboard
with two tabs (user request 2026-10-26).

Two pages with a shared bar rather than one document or an iframe, on purpose:
both pages declare the same CSS custom properties and element ids (both have a
search box id="q"), and each relies on things a frame breaks -- sticky table
headers, in-page jump links to each market, and its own print/Save-as-PDF
layout. As separate pages every one of those keeps working untouched.

The bar uses the colour tokens both pages already define (--head2), so it
follows their light/dark themes, and it is hidden in print.

Only the copies in the stage directory are edited. dr_dashboard.html and the
RRG source stay exactly as built, and the GitHub Pages copies are unaffected.
The RRG file is published as it was last built: this does not rebuild it.
"""
import sys
from pathlib import Path

MARK = "yr-nav"   # present once injected; makes the script safe to re-run

CSS = """<style>
.yr-nav{background:var(--head2,#163C7E)}
.yr-nav-in{max-width:%(w)s;margin:0 auto;padding:0 %(pad)s;display:flex;gap:4px;flex-wrap:wrap}
.yr-nav a{color:#fff;opacity:.72;text-decoration:none;font:600 13px/1 "Segoe UI",Arial,"Helvetica Neue",sans-serif;
  padding:12px 14px 9px;border-bottom:3px solid transparent;letter-spacing:.01em}
.yr-nav a:hover{opacity:1}
.yr-nav a[aria-current="page"]{opacity:1;border-bottom-color:#fff}
.yr-nav a:focus-visible{outline:2px solid #fff;outline-offset:-4px}
@media print{.yr-nav{display:none !important}}
</style>
"""


def bar(active: str, width: str, pad: str, rrg_href: str = "rrg.html") -> str:
    def tab(href, label, key):
        cur = ' aria-current="page"' if key == active else ""
        return f'<a href="{href}"{cur}>{label}</a>'
    return (CSS % {"w": width, "pad": pad}
            + f'<nav class="{MARK}" aria-label="Dashboards"><div class="yr-nav-in">'
            + tab("./", "DR Dashboard", "dash") + tab(rrg_href, "DR RRG", "rrg")
            + "</div></nav>\n")


def main(stage: Path, rrg_src: Path | None, rrg_href: str = "rrg.html") -> int:
    """rrg_src None = tab bar on index.html only. The weekly Netlify deploy does that
    once the DR RRG page comes from GitHub Pages through a rewrite (_redirects)."""
    index = stage / "index.html"
    # The GitHub Actions job stages rrg.html only (it never handles the DR
    # dashboard's file), so there may be no index.html to edit.
    html = index.read_text(encoding="utf-8") if index.exists() else MARK
    if MARK not in html:
        # The DR dashboard has no <body> tag (it is deliberately in quirks mode), so the
        # bar goes straight after its one stylesheet, ahead of <div class="wrap">.
        cut = html.index("</style>") + len("</style>")
        html = html[:cut] + "\n" + bar("dash", "1180px", "20px", rrg_href) + html[cut:]
        index.write_text(html, encoding="utf-8")

    if rrg_src is None:
        print(f"tab bar added: index.html in {stage}")
        return 0
    rrg = rrg_src.read_text(encoding="utf-8")
    if MARK not in rrg:
        cut = rrg.index("<body>") + len("<body>")
        rrg = rrg[:cut] + "\n" + bar("rrg", "1000px", "16px") + rrg[cut:]
    (stage / "rrg.html").write_text(rrg, encoding="utf-8")
    print(f"tab bar added: {'index.html + ' if index.exists() else ''}rrg.html ({len(rrg):,} bytes) in {stage}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    href = "rrg.html"
    if "--rrg-href" in args:
        i = args.index("--rrg-href")
        href = args[i + 1]
        del args[i:i + 2]
    sys.exit(main(Path(args[0]), Path(args[1]) if len(args) > 1 else None, href))
