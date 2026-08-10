# =============================================================================
# make_report.R  --  one browsable HTML page with every figure and table
# Writes natcities_visual_items/report.html. Images are referenced relatively
# (figures/<name>.png), so the file stays a few KB and always shows whatever
# the last build produced -- open it from inside natcities_visual_items/.
#
#   Rscript make_report.R        # after build_all.R
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

REPORT_OUT <- file.path(VIS_ROOT, "report.html")

esc <- function(x) {
  x <- as.character(x); x[is.na(x)] <- ""
  x <- gsub("&", "&amp;", x, fixed = TRUE)
  x <- gsub("<", "&lt;",  x, fixed = TRUE)
  gsub(">", "&gt;", x, fixed = TRUE)
}

FIGURES <- list(
  list(sec = "Main figures", name = "fig1_risk_effectiveness", slot = "fig:result1",
       title = "Risk and effectiveness across the climate gradient",
       cap = "Baseline heat mortality against warm-season temperature (log-log), then the
              mortality reduction and avoided deaths delivered by each pathway. One point
              per city; AC is net of its waste-heat feedback."),
  list(sec = "Main figures", name = "fig2_distribution", slot = "fig:result2",
       title = "Who pays, and who benefits",
       cap = "Public versus private cost per capita by lever; the equity-efficiency
              trade-off of targeting cooling support by income; and whether an explicit
              equity rule makes greening more progressive than a uniform one."),
  list(sec = "Main figures", name = "fig3_costeffectiveness", slot = "fig:result3",
       title = "Cost-effectiveness and budget choice",
       cap = "Euros per death avoided by pathway; the budget frontier normalised per
              capita so 40 cities share one pair of axes; and the order in which a
              benefit-maximising city buys the three levers."),
  list(sec = "Main figures", name = "fig4_synergies", slot = "fig:result4",
       title = "Synergies and trade-offs between the levers",
       cap = "How much of air conditioning's benefit its own waste heat takes back, how
              little of that greening offsets, and how far the two levers overlap."),
  list(sec = "Supplementary figures", name = "si1_sensitivity", slot = "SI",
       title = "Sensitivity of the cost-effectiveness ranking",
       cap = "Discount rate, greening cost assumptions and greening ambition. None of
              them reorders the three levers."),
  list(sec = "Supplementary figures", name = "si2_if_comparison", slot = "SI",
       title = "Exposure-response function sensitivity",
       cap = "The four temperature-mortality curves the runs ship, and what choosing
              between them does to baseline heat mortality."),
  list(sec = "Supplementary figures", name = "si3_ews", slot = "SI",
       title = "The early-warning pathway in detail",
       cap = "Warning days to 2050, the share of heat deaths falling on warned days, and
              the behavioural take-up ramp behind the benefits."))

TABLES <- list(
  list(name = "tab_cba_by_city",       title = "Cost-benefit by city and pathway",
       cap = "Three rows per city. A blank cost per death marks a pathway avoiding
              fewer than 0.5 deaths over 25 years."),
  list(name = "tab_risk_by_city",      title = "Hazard, exposure and baseline risk by city",
       cap = "Climate metrics from the 2020 baseline hazard, with the model's own
              exposure population as denominator."),
  list(name = "tab_ews_by_city",       title = "The early-warning pathway by city",
       cap = "`Attribution' records which wording that city's run produced for the EWS
              row; all three carry the same central estimate."),
  list(name = "tab_synergies_by_city", title = "Lever interactions by city",
       cap = "The quantities behind Fig 4, city by city."))

# One <img> block per figure, skipped silently if that figure was not rendered.
figure_html <- function(f) {
  png <- file.path(FIG_DIR, paste0(f$name, ".png"))
  if (!file.exists(png)) return(NULL)
  sprintf(paste0(
    '<figure id="%s">\n<figcaption><span class="slot">%s</span>',
    '<h3>%s</h3><p>%s</p></figcaption>\n',
    '<a href="figures/%s.png"><img src="figures/%s.png" alt="%s"></a>\n',
    '<p class="files">figures/%s.png &middot; ',
    '<a href="figures/%s.pdf">PDF</a></p>\n</figure>'),
    f$name, esc(f$slot), esc(f$title), esc(gsub("\\s+", " ", f$cap)),
    f$name, f$name, esc(f$title), f$name, f$name)
}

# CSV -> a scrollable <table> with a sticky header. Numeric columns are right
# aligned so the digits line up the way they do in the LaTeX version.
table_html <- function(t) {
  csv <- file.path(TAB_DIR, paste0(t$name, ".csv"))
  if (!file.exists(csv)) return(NULL)
  d <- suppressWarnings(readr::read_csv(csv, show_col_types = FALSE, progress = FALSE))
  d <- as.data.frame(d)
  num <- vapply(d, is.numeric, logical(1))
  cls <- ifelse(num, ' class="n"', "")
  head_r <- paste0(sprintf("<th%s>%s</th>", cls, esc(names(d))), collapse = "")
  body_r <- vapply(seq_len(nrow(d)), function(i) {
    cells <- vapply(seq_along(d), function(j) {
      v <- d[i, j]
      sprintf("<td%s>%s</td>", cls[j],
              if (is.na(v)) '<span class="na">&mdash;</span>' else esc(v))
    }, character(1))
    paste0("<tr>", paste0(cells, collapse = ""), "</tr>")
  }, character(1))
  sprintf(paste0(
    '<section id="%s">\n<h3>%s</h3><p class="cap">%s</p>\n',
    '<p class="files">%d rows &middot; <a href="tables/%s.csv">CSV</a> &middot; ',
    '<a href="tables/%s.tex">LaTeX</a></p>\n',
    '<div class="tw"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>\n',
    '</section>'),
    t$name, esc(t$title), esc(gsub("\\s+", " ", t$cap)), nrow(d),
    t$name, t$name, head_r, paste0(body_r, collapse = "\n"))
}

CSS <- '
:root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e0dfda;--card:#fff;
      --accent:#1565C0;--code:#f2f1ec}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#16171a;--fg:#e8e8e4;--muted:#9a9a94;--line:#2c2e33;--card:#1e1f23;
  --accent:#7aa9e8;--code:#24262b}}
:root[data-theme="dark"]{--bg:#16171a;--fg:#e8e8e4;--muted:#9a9a94;--line:#2c2e33;
  --card:#1e1f23;--accent:#7aa9e8;--code:#24262b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:2.5rem 1.25rem 5rem}
h1{font-size:1.7rem;margin:0 0 .3rem;letter-spacing:-.01em}
h2{font-size:1.1rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:3.5rem 0 1.25rem;padding-bottom:.5rem;border-bottom:1px solid var(--line)}
h3{font-size:1.05rem;margin:0 0 .3rem}
.sub{color:var(--muted);margin:0 0 1.5rem}
.meta{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:.9rem 1.1rem;font-size:.85rem;color:var(--muted)}
.meta code{background:var(--code);padding:.1em .4em;border-radius:4px;
  font-size:.92em;word-break:break-all}
nav{margin:1.5rem 0 0;font-size:.88rem}
nav a{display:inline-block;margin:0 .9rem .5rem 0;color:var(--accent);text-decoration:none}
nav a:hover{text-decoration:underline}
figure{margin:0 0 3rem;background:var(--card);border:1px solid var(--line);
  border-radius:10px;padding:1.25rem}
figcaption p{color:var(--muted);font-size:.88rem;margin:.35rem 0 1rem;max-width:80ch}
.slot{display:inline-block;font-size:.7rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--accent);border:1px solid var(--accent);border-radius:4px;
  padding:.1em .45em;margin-bottom:.5rem}
img{width:100%;height:auto;display:block;border-radius:6px;background:#fff}
.files{font-size:.78rem;color:var(--muted);margin:.7rem 0 0;font-family:ui-monospace,
  SFMono-Regular,Menlo,monospace}
.files a{color:var(--accent)}
section{margin:0 0 3rem;background:var(--card);border:1px solid var(--line);
  border-radius:10px;padding:1.25rem}
.cap{color:var(--muted);font-size:.88rem;margin:.35rem 0 0;max-width:80ch}
.tw{overflow:auto;max-height:26rem;margin-top:1rem;border:1px solid var(--line);
  border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.82rem;
  font-variant-numeric:tabular-nums}
th,td{padding:.4rem .65rem;text-align:left;white-space:nowrap;
  border-bottom:1px solid var(--line)}
th{position:sticky;top:0;background:var(--card);font-weight:600;z-index:1;
  box-shadow:inset 0 -1px 0 var(--line)}
td.n,th.n{text-align:right}
tbody tr:hover{background:var(--code)}
.na{color:var(--muted)}
footer{margin-top:4rem;padding-top:1.25rem;border-top:1px solid var(--line);
  color:var(--muted);font-size:.82rem}
'

build_report <- function() {
  banner("HTML report")
  meta <- load_city_meta()
  n_city <- if (is.null(meta)) NA_integer_ else nrow(meta)

  figs <- Filter(Negate(is.null), lapply(FIGURES, function(f) {
    h <- figure_html(f); if (is.null(h)) NULL else list(sec = f$sec, html = h,
      nav = sprintf('<a href="#%s">%s</a>', f$name, esc(f$title)))
  }))
  # Exemplar dashboards are discovered rather than listed: which cities are
  # medoids changes with the city set.
  ex <- list.files(FIG_DIR, pattern = "^exemplar_.*\\.png$")
  ex_html <- vapply(sort(ex), function(p) {
    city <- sub("^exemplar_(.*)\\.png$", "\\1", p)
    cl <- if (!is.null(meta)) as.character(meta$climate_cluster[meta$city == city]) else ""
    figure_html(list(name = sub("\\.png$", "", p), slot = "Exemplar",
      title = sprintf("%s — %s cluster medoid", city_label(city),
                      if (length(cl) && !is.na(cl[1])) cl[1] else "?"),
      cap = "Single-city dashboard: baseline mortality trajectory, avoided deaths by
             pathway, district greening against vulnerability, and the cost-benefit
             frontier with constrained portfolios."))
  }, character(1), USE.NAMES = FALSE)

  tabs <- Filter(Negate(is.null), lapply(TABLES, function(t) {
    h <- table_html(t); if (is.null(h)) NULL else list(html = h,
      nav = sprintf('<a href="#%s">%s</a>', t$name, esc(t$title)))
  }))

  sec_html <- function(title, bodies)
    if (!length(bodies)) "" else
      sprintf("<h2>%s</h2>\n%s", title, paste0(bodies, collapse = "\n"))

  pick <- function(s) vapply(Filter(function(f) f$sec == s, figs),
                             function(f) f$html, character(1))

  html <- sprintf('<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Urban heat adaptation — figures and tables</title>
<style>%s</style></head><body><div class="wrap">
<h1>Synergies and trade-offs of public and private urban heat adaptation</h1>
<p class="sub">Figures and tables for the Nature Cities results manuscript.</p>
<div class="meta">
<strong>%s cities</strong> &middot; built %s<br>
Inputs: <code>%s</code><br>
Regenerate: <code>Rscript build_all.R</code> then <code>Rscript make_report.R</code>
</div>
<nav>%s</nav>
%s
%s
%s
<footer>Generated by <code>natcities_visual_items/R/make_report.R</code>.
Images link to the PDF version used for typesetting.</footer>
</div></body></html>',
    CSS,
    if (is.na(n_city)) "?" else n_city,
    format(Sys.Date(), "%Y-%m-%d"),
    esc(OUTPUTS_BASE),
    paste0(c(vapply(figs, function(f) f$nav, character(1)),
             vapply(tabs, function(t) t$nav, character(1))), collapse = ""),
    sec_html("Main figures", pick("Main figures")),
    sec_html("Supplementary figures", pick("Supplementary figures")),
    paste0(sec_html("Exemplar cities", ex_html), "\n",
           sec_html("Tables", vapply(tabs, function(t) t$html, character(1)))))

  writeLines(html, REPORT_OUT, useBytes = TRUE)
  message(sprintf("  [saved] report.html  (%d figures, %d exemplars, %d tables, %.0f KB)",
                  length(figs), length(ex_html), length(tabs),
                  file.size(REPORT_OUT) / 1024))
  invisible(REPORT_OUT)
}

if (!exists(".NATCITIES_NORUN")) build_report()
