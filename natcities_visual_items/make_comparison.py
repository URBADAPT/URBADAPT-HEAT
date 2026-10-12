"""Build a self-contained HTML comparing the published and corrected figure sets.

Renders both runs side by side with the caption numbers the R build emits, so the
manuscript edits can be decided from one page rather than by flipping between two
figure directories. Images are base64-embedded, so the output is a single portable
file that can be mailed to a co-author.

Expects compare/published/ and compare/corrected/ to each hold the eight PNGs and a
build.log, which build_all.R produces when pointed at the respective output trees.

Usage:
    python make_comparison.py
"""

from __future__ import annotations

import base64
import datetime
import html
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
# The compare/ tree lives wherever the figure sets were collected, which need not be
# beside this script once it is run from a worktree.
CMP = pathlib.Path(os.environ.get("NATCITIES_COMPARE_DIR", ROOT / "compare"))

TITLES = {
    "fig1_risk_costs_portfolios": ("Fig 1 - risk, costs, portfolios", "fig:result1"),
    "fig1b_outcome_profiles": ("Fig 1b - standardised outcome profiles", "fig:profiles"),
    "fig2_distribution": ("Fig 2 - who pays / equity", "fig:fig2"),
    "fig3_synergies": ("Fig 3 - synergies", "fig:fig3"),
    "fig4_city_maps": ("Fig 4 - city maps", "fig:fig4"),
    "si1_sensitivity": ("SI 1 - sensitivity", "fig:si_sens"),
    "si2_if_comparison": ("SI 2 - impact-function comparison", "fig:si_if"),
    "si3_ews": ("SI 3 - early-warning systems", "fig:si_ews"),
}

# Caption lines wrap in the R console output; these prefixes mark a continuation
# rather than a new caption.
CONT = ("Temperate", "measured", "equity", "31 cities", "coefficients", "(median")

# Derived by compute_deltas.py from both output trees, each scored on its own climate
# class, rather than typed in. Several of these are functions of the class, which moved
# when it was re-based on observed ERA5-Land, so a literal written beforehand goes
# quietly stale: the trees row sat at x10.2 and recomputes to x8.5, and fig1a was
# recorded at x1.23 against the x1.26 the rebuild emits.
DELTAS = [
    ("Climate class basis",
     "model 2020 baseline (a climatological mean)",
     "observed ERA5-Land distribution",
     "the old basis counted threshold crossings on a variance-free series: "
     "Vienna 0 hot days against ~15 observed. 5 cities move; Hot 10&rarr;8", True),
    ("Baseline range (per 100k)",
     "0.02 (Dublin) to 168.2 (Athens)<br><b>4.0 orders of magnitude</b>",
     "0.85 (Dublin) to 113.7 (Athens)<br><b>2.1 orders of magnitude</b>",
     "Masselot spans 1.7 orders across the same 40 cities", True),
    ("Fig 1a climate gradient",
     "&times;1.58 per &deg;C (R&sup2;=0.69)", "&times;1.26 per &deg;C (R&sup2;=0.62)",
     "annotation is inside the panel, so it regenerated itself", True),
    ("AC hot&rarr;cool cost gradient", "&times;15.3", "&times;2.6",
     "median cost per avoided death, by climate class", True),
    ("Trees hot&rarr;cool", "&times;12.1", "&times;8.5",
     "least affected pathway - now the steepest", False),
    ("EWS hot&rarr;cool", "&times;5.8", "&times;1.7", "gradient nearly disappears", True),
    ("Fig 4a synergy slope",
     "+3.2 pp per &deg;C (R&sup2;=0.35)", "+3.3 pp per &deg;C (R&sup2;=0.58)",
     "slope unchanged, fit much tighter", False),
    ("Equity: benefit shifted",
     "15.2 pp for 4.8% of lives saved", "15.0 pp for 3.4% of lives saved",
     "equity result holds; cost in lives slightly lower", False),
    ("Waste heat cancelled by greening",
     "median 2.6%; outweighs greening in 7 of 31",
     "median 3.5%; outweighs greening in 4 of 40", "", False),
    ("Masselot tail extension binds",
     "3 of 40 cities (max 0.4%)", "0 of 40 cities (max 0.0%)",
     "the tail sensitivity is formally inert under the correction", False),
]


def captions(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    out, buf = [], None
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "[caption]" in ln:
            if buf:
                out.append(buf)
            buf = ln.split("[caption]", 1)[1].strip()
        elif buf is not None and ln.startswith(CONT):
            buf += " " + ln.strip()
    if buf:
        out.append(buf)
    return out


def b64(p: pathlib.Path) -> str:
    return base64.b64encode(p.read_bytes()).decode()


def main() -> int:
    pub_caps = captions(CMP / "published" / "build.log")
    cor_caps = captions(CMP / "corrected" / "build.log")

    rows = "\n".join(
        '<tr class="{}"><td class="k">{}</td><td class="old">{}</td>'
        '<td class="new">{}</td><td class="note">{}</td></tr>'.format(
            "big" if big else "", k, o, n, note)
        for k, o, n, note, big in DELTAS
    )

    figs = []
    for stem, (title, label) in TITLES.items():
        old, new = CMP / "published" / f"{stem}.png", CMP / "corrected" / f"{stem}.png"
        if not (old.exists() and new.exists()):
            continue
        figs.append(
            '<section class="fig"><h2>{t} <span class="lbl">{l}</span></h2>'
            '<div class="pair">'
            '<figure><figcaption class="old">published spec</figcaption>'
            '<img src="data:image/png;base64,{a}" alt="{s} published"></figure>'
            '<figure><figcaption class="new">corrected spec</figcaption>'
            '<img src="data:image/png;base64,{b}" alt="{s} corrected"></figure>'
            "</div></section>".format(t=html.escape(title), l=label, s=stem,
                                      a=b64(old), b=b64(new))
        )

    cap_rows = ""
    for i in range(max(len(pub_caps), len(cor_caps))):
        o = html.escape(pub_caps[i]) if i < len(pub_caps) else ""
        n = html.escape(cor_caps[i]) if i < len(cor_caps) else ""
        cap_rows += '<tr class="{}"><td class="old">{}</td><td class="new">{}</td></tr>\n'.format(
            "" if o == n else "diff", o, n)

    css = """
:root{--bg:#fbfbfa;--fg:#1a1a18;--mut:#6b6b66;--line:#e2e1dd;--old:#9a5b2e;--new:#1f6f4a;--card:#fff;--hl:#fff8e6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#16161a;--fg:#ecebe8;--mut:#9b9a95;--line:#2e2e34;--old:#d99a63;--new:#6fc39a;--card:#1e1e24;--hl:#2a2616}}
:root[data-theme="dark"]{--bg:#16161a;--fg:#ecebe8;--mut:#9b9a95;--line:#2e2e34;--old:#d99a63;--new:#6fc39a;--card:#1e1e24;--hl:#2a2616}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.01em}
.sub{color:var(--mut);margin:0 0 28px;font-size:14px}
h2{font-size:17px;margin:0 0 12px;font-weight:600}
.lbl{color:var(--mut);font-weight:400;font-size:13px;font-family:ui-monospace,monospace}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-bottom:34px}
th,td{padding:10px 13px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}
th{font-weight:600;font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);background:var(--bg)}
tr:last-child td{border-bottom:none}
tr.big td{background:var(--hl)}
td.k{font-weight:600;width:24%}
td.old{color:var(--old);width:26%}
td.new{color:var(--new);width:26%}
td.note{color:var(--mut);font-size:13px}
.fig{margin:0 0 42px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.pair{grid-template-columns:1fr}}
figure{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}
figcaption{font-size:12px;letter-spacing:.04em;text-transform:uppercase;font-weight:600;margin-bottom:8px}
figcaption.old{color:var(--old)}figcaption.new{color:var(--new)}
img{width:100%;height:auto;display:block;border-radius:5px;background:#fff}
tr.diff td{background:var(--hl)}
.caps td{font-size:12.5px;font-family:ui-monospace,monospace;line-height:1.45}
.foot{color:var(--mut);font-size:13px;border-top:1px solid var(--line);padding-top:18px;margin-top:10px}
code{font-family:ui-monospace,monospace;font-size:.93em;background:var(--bg);padding:1px 5px;border-radius:4px}
"""

    doc = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hazard Correction Comparison</title><style>%s</style></head><body><div class="wrap">
<h1>Hazard correction &mdash; published vs corrected</h1>
<p class="sub">All 40 cities, NB01&ndash;08 re-run on JUNO with the variance-corrected impact functions
(<code>masselot_tail_varcorr</code>) and the ERA5-Land reference bias correction
(<code>t2m_era5_bias_correct</code>). The published column reproduces the current manuscript
numbers exactly, which is what makes the differences attributable. Generated %s.</p>
<p class="sub"><b>The corrected column carries two changes, not one.</b> Besides the hazard
corrections, the climate class is now read off the observed ERA5-Land distribution rather than
the model's own 2020 baseline, because the old basis counted days above 25&nbsp;&deg;C on a
variance-free climatological series &mdash; the same Jensen fault the variance correction fixes,
sitting in the grouping variable. It put Vienna at 0 hot days against roughly 15 in the observed
record. Five cities change class (Paris, Ljubljana, Sofia to Cool; Barcelona, Milan to Temperate)
and the medoids become Hamburg / Budapest / Thessaloniki. Every per-class row below is therefore a
joint effect; each column is scored on its own class, so none of the class change is misattributed
to the corrections.</p>

<h2>Numbers that change</h2>
<table><thead><tr><th>Quantity</th><th>Published</th><th>Corrected</th><th>Note</th></tr></thead>
<tbody>%s</tbody></table>

<h2>Figures</h2>
%s

<h2>Build captions, line by line</h2>
<p class="sub">Every caption number the build emits, for diffing against the LaTeX. Highlighted rows differ.</p>
<table class="caps"><thead><tr><th>Published</th><th>Corrected</th></tr></thead><tbody>
%s</tbody></table>

<p class="foot">Caveats these figures inherit: the per-capita framing is contaminated for the eight cities
whose modelled domain is much smaller than Masselot's URAU (Paris, Lisbon, Athens, Naples, Milan, Lyon,
Porto, Barcelona), so their rates are not comparable to the source study; Athens remains unexplained at
113.7 per 100k and still anchors the top of the range; and eight cities sit below 25%% greening-coefficient
coverage, which matters more now that trees is the pathway with the steepest surviving climate gradient.</p>
</div></body></html>""" % (css, datetime.date.today().isoformat(), rows, "".join(figs), cap_rows)

    out = CMP / "comparison.html"
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {out.stat().st_size/1e6:.1f} MB | {len(figs)} figure pairs | "
          f"{len(DELTAS)} headline deltas | captions: {len(pub_caps)} vs {len(cor_caps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
