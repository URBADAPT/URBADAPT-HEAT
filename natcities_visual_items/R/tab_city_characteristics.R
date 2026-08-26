# =============================================================================
# tab_city_characteristics.R  --  SI table: per-city characterisation
#
# The descriptors that used to be a grey annotation strip on Fig 1 panel b.
# Four more columns on top of a 40-row heatmap was more than a main-text panel
# could carry, so the per-city detail lives here instead: climate metrics, the
# greening-cooling coefficient coverage that conditions every tree-pathway
# number, and the Local Climate Zone composition of each Functional Urban Area.
#
# Emitted as a longtable (40 rows will not fit a float), so the LaTeX side must
# \input it WITHOUT wrapping it in a table environment -- it carries its own
# caption and label, like tables/ews_taxonomy_table.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_tab_city_characteristics <- function() {
  banner("Table: per-city characterisation (SI)")
  meta <- load_city_meta()
  if (is.null(meta) || !nrow(meta)) {
    message("No city metadata -- run 00_city_meta.R first."); return(invisible(NULL)) }

  need <- c("city_label", "country", "pop_k", "warmseason_mean_t2m", "hot_days",
            "climate_cluster", "coef_lst_coverage_pct", "lcz_compact_pct",
            "lcz_open_pct", "lcz_other_built_pct", "lcz_built_pct",
            "lcz_diversity")
  miss <- setdiff(need, names(meta))
  if (length(miss)) {
    message(sprintf("  [skip] metadata lacks: %s", paste(miss, collapse = ", ")))
    return(invisible(NULL))
  }

  # Hottest first: the same ordering as tab_risk_by_city, so the two SI tables
  # can be read side by side.
  d <- meta[order(-meta$warmseason_mean_t2m), need, drop = FALSE]

  out <- data.frame(
    City        = d$city_label,
    Country     = d$country,
    `Pop (k)`   = round(d$pop_k),
    Class       = as.character(d$climate_cluster),
    # A literal degree sign: inputenc utf8 + textcomp are both loaded by the
    # manuscript, and it keeps the CSV header readable too.
    `T2M (°C)`  = round(d$warmseason_mean_t2m, 1),
    `Hot days`  = d$hot_days,
    `Coef. cov. (%)` = d$coef_lst_coverage_pct,
    `Compact (%)`    = d$lcz_compact_pct,
    `Open (%)`       = d$lcz_open_pct,
    `Other (%)`      = d$lcz_other_built_pct,
    `Built (%)`      = d$lcz_built_pct,
    `LCZ div.`       = d$lcz_diversity,
    check.names = FALSE, stringsAsFactors = FALSE)

  cap <- paste(
    "Per-city characterisation of the 40-city sample, ordered by descending",
    "warm-season mean temperature.",
    "\\emph{Class} is the descriptive climate cluster (k-means on warm-season mean",
    "2\\,m temperature and hot-day count).",
    "\\emph{Coef. cov.} is the share of summer (May--September) Local Climate Zone",
    "months with a non-zero greening-cooling coefficient; it conditions every",
    "tree-pathway result and should be read alongside any per-city greening number.",
    "\\emph{Compact}, \\emph{Open} and \\emph{Other} are the LCZ 1--3, 4--6 and 7--10",
    "shares of built land and sum to 100; \\emph{Built} is the LCZ 1--10 share of the",
    "whole Functional Urban Area, i.e. the built--natural balance and the part of the",
    "city the greening policy can act on.",
    "\\emph{LCZ div.} is the effective number of LCZ classes, $\\exp$ of the Shannon",
    "entropy over all valid cells.")

  # digits is per column, offset by one for the (suppressed) row-name column:
  # counts render as integers, shares to one decimal, LCZ diversity to two.
  save_table(out, "tab_city_characteristics", caption = cap,
             label = "tab:city_characteristics",
             digits = c(0, 0, 0, 0, 0, 1, 0, 1, 1, 1, 1, 1, 2),
             align = c("l", "l", "l", "r", "l", "r", "r", "r", "r", "r", "r", "r", "r"),
             longtable = TRUE, size = "\\scriptsize")

  low <- out$City[!is.na(out$`Coef. cov. (%)`) & out$`Coef. cov. (%)` < 25]
  message(sprintf("  %d cities; %d below 25%% coefficient coverage%s",
                  nrow(out), length(low),
                  if (length(low)) paste0(": ", paste(low, collapse = ", ")) else ""))
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_city_characteristics()
