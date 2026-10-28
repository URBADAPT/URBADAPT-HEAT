# =============================================================================
# si6_svi_budget.R  --  SI: the same budget spent on AC or on street trees,
# allocated to the most socially vulnerable units first (notebook 11)
#
#   (a) Cost per death avoided against the per-resident budget.
#   (b) Deaths avoided in the two most vulnerable SVI quintiles per 100k.
#   (c) What SVI priority adds to those quintiles' share of the benefit, over
#       each pathway's own rule (income for AC, greenness deficit for trees).
# Rows a-c are small multiples by climate class; lines are the median city with
# the interquartile range shaded. Trees are drawn for both ceilings.
#
# Source: tables/svi_budget_sweep_<city>.csv under SVI_SWEEP_TREE (NB11 outputs).
# Also writes tab_svi_budget (one row per budget level, all cities pooled).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

SVI_SWEEP_TREE <- Sys.getenv("SVI_SWEEP_TREE",
  "C:/Users/giaco/OneDrive - IIASA/Documents/juno/svi_40")

SERIES <- c(ac_svi = "AC", trees_svi_abs = "Trees, ceiling GVI 35",
            trees_svi = "Trees, city ceiling")
SERIES_COLORS <- c("AC" = PATHWAY_COLORS[["AC"]],
                   "Trees, ceiling GVI 35" = PATHWAY_COLORS[["Trees"]],
                   "Trees, city ceiling" = PATHWAY_COLORS[["Trees"]])
SERIES_LINES  <- c("AC" = "solid", "Trees, ceiling GVI 35" = "solid", "Trees, city ceiling" = "22")
SERIES_SHAPES <- c("AC" = 16, "Trees, ceiling GVI 35" = 17, "Trees, city ceiling" = 2)
GAIN <- c("AC: SVI vs income priority" = PATHWAY_COLORS[["AC"]],
          "Trees: SVI vs greenness-deficit priority" = PATHWAY_COLORS[["Trees"]])

read_sweep <- function() {
  f <- list.files(SVI_SWEEP_TREE, pattern = "^svi_budget_sweep_[a-z]+\\.csv$",
                  recursive = TRUE, full.names = TRUE)
  f <- f[basename(dirname(f)) == "tables"]   # per-city files only, not the pooled summaries
  if (!length(f)) stop("no svi_budget_sweep_<city>.csv under ", SVI_SWEEP_TREE)
  d <- dplyr::bind_rows(lapply(f, readr::read_csv, show_col_types = FALSE))
  d$q45 <- d$share_Q4 + d$`share_Q5-High`
  # Gross of the AC waste-heat penalty, which the model does not attribute to quintiles.
  d$q45_100k <- d$avoided_25y_gross * d$q45 / d$pop_city * 1e5
  d
}

# Median city and interquartile range per budget, class and series.
band <- function(df, y, positive = TRUE) {
  df |>
    dplyr::filter(is.finite(.data[[y]]), !positive | .data[[y]] > 0) |>
    dplyr::group_by(climate_cluster, series, budget_pc_eur) |>
    dplyr::summarise(med = median(.data[[y]]), lo = quantile(.data[[y]], 0.25),
                     hi = quantile(.data[[y]], 0.75), n = dplyr::n(), .groups = "drop")
}

budget_scale <- function() {
  scale_x_log10(name = "Budget per resident (€, present value, log)",
                breaks = c(25, 100, 400, 1600), labels = function(v) paste0("€", scales::comma(v)))
}

build_si6 <- function() {
  banner("SI 6: equal budgets for AC and trees, allocated by social vulnerability")
  d <- attach_meta(read_sweep())
  message(sprintf("  %d cities, %d budget levels", dplyr::n_distinct(d$city), dplyr::n_distinct(d$budget_pc_eur)))

  s <- d[d$strategy %in% names(SERIES), , drop = FALSE]
  s$series <- factor(SERIES[s$strategy], levels = SERIES)

  line_layers <- list(
    geom_ribbon(aes(ymin = lo, ymax = hi, fill = series), alpha = 0.14, color = NA),
    geom_line(aes(linetype = series), linewidth = 0.8),
    geom_point(aes(shape = series), size = 1.9),
    scale_color_manual(values = SERIES_COLORS, name = NULL),
    scale_fill_manual(values = SERIES_COLORS, guide = "none"),
    scale_linetype_manual(values = SERIES_LINES, name = NULL),
    scale_shape_manual(values = SERIES_SHAPES, name = NULL),
    facet_wrap(~climate_cluster, nrow = 1),
    budget_scale(), theme_multicity())

  # The city-ceiling trees only carry their own ribbon-free line; one band per pathway.
  ba <- band(s, "cost_per_death_eur")
  ba$lo[ba$series == "Trees, city ceiling"] <- NA
  ba$hi[ba$series == "Trees, city ceiling"] <- NA
  pa <- ggplot(ba, aes(budget_pc_eur, med, color = series, group = series)) + line_layers +
    eur_log_scale("Cost per death avoided (€, log)") + labs(tag = "a")

  bb <- band(s, "q45_100k")
  bb$lo[bb$series == "Trees, city ceiling"] <- NA
  bb$hi[bb$series == "Trees, city ceiling"] <- NA
  pb <- ggplot(bb, aes(budget_pc_eur, med, color = series, group = series)) + line_layers +
    scale_y_log10(name = "Deaths avoided in the two most\nvulnerable quintiles per 100k (log)",
                  labels = scales::label_number(accuracy = 0.1)) + labs(tag = "b")

  g <- function(a, b, lab) {
    x <- d[d$strategy == a, c("city", "climate_cluster", "budget_pc_eur", "q45")]
    y <- d[d$strategy == b, c("city", "budget_pc_eur", "q45")]
    m <- dplyr::inner_join(x, y, by = c("city", "budget_pc_eur"), suffix = c("", "_base"))
    m$gain <- 100 * (m$q45 - m$q45_base)
    m$series <- lab
    m
  }
  gc <- dplyr::bind_rows(g("ac_svi", "ac_income", names(GAIN)[1]),
                         g("trees_svi_abs", "trees_deficit_abs", names(GAIN)[2]))
  gc$series <- factor(gc$series, levels = names(GAIN))
  bc <- band(gc, "gain", positive = FALSE)
  pc <- ggplot(bc, aes(budget_pc_eur, med, color = series, fill = series, group = series)) +
    geom_hline(yintercept = 0, color = "grey60", linewidth = 0.4) +
    geom_ribbon(aes(ymin = lo, ymax = hi), alpha = 0.14, color = NA) +
    geom_line(linewidth = 0.8) + geom_point(aes(shape = series), size = 1.9) +
    scale_color_manual(values = GAIN, name = NULL) +
    scale_fill_manual(values = GAIN, guide = "none") +
    scale_shape_manual(values = c(16, 17), name = NULL) +
    facet_wrap(~climate_cluster, nrow = 1) + budget_scale() +
    scale_y_continuous("Gain in their share of the\nbenefit (percentage points)") +
    labs(tag = "c") + theme_multicity()

  # Rows a and b share series and the budget axis: one legend under b, one axis title at the bottom.
  fig <- (pa + theme(legend.position = "none", axis.title.x = element_blank())) /
    (pb + theme(axis.title.x = element_blank())) / pc
  save_item(fig, "si6_svi_budget", width = 11, height = 11.5)

  # --- table: one row per budget level, all cities pooled ---------------------
  w <- tidyr::pivot_wider(d[, c("city", "budget_pc_eur", "strategy", "cost_per_death_eur", "q45", "q45_100k", "saturated")],
                          names_from = strategy, values_from = c(cost_per_death_eur, q45, q45_100k, saturated))
  tab <- w |>
    dplyr::group_by(budget_pc_eur) |>
    dplyr::summarise(
      ac = median(cost_per_death_eur_ac_svi, na.rm = TRUE) / 1e6,
      tr = median(cost_per_death_eur_trees_svi_abs[is.finite(cost_per_death_eur_trees_svi_abs)]) / 1e6,
      cheaper = sprintf("%d of %d", sum(cost_per_death_eur_trees_svi_abs < cost_per_death_eur_ac_svi, na.rm = TRUE),
                        sum(is.finite(cost_per_death_eur_trees_svi_abs))),
      ratio = median((q45_100k_ac_svi / q45_100k_trees_svi_abs)[q45_100k_trees_svi_abs > 0], na.rm = TRUE),
      gain_ac = median(100 * (q45_ac_svi - q45_ac_income), na.rm = TRUE),
      gain_tr = median(100 * (q45_trees_svi_abs - q45_trees_deficit_abs), na.rm = TRUE),
      sat = sprintf("%d / %d", sum(saturated_trees_svi), sum(saturated_trees_svi_abs)),
      .groups = "drop")
  names(tab) <- c("Budget (€/resident)", "AC (M€/death)", "Trees (M€/death)", "Trees cheaper",
                  "Vulnerable deaths, AC/trees", "SVI gain, AC (pp)", "SVI gain, trees (pp)",
                  "Trees saturated (city / GVI 35)")
  save_table(tab, "tab_svi_budget",
             caption = paste(
               "Equal budgets for household AC and street trees, allocated to the most socially vulnerable",
               "units first (40 cities). Each row spends the same present-value budget per resident on each",
               "pathway. Costs per death avoided are medians over the cities where the pathway avoids deaths;",
               "trees use the GVI 35 ceiling. Vulnerable deaths are those avoided over 25 years in the two",
               "most vulnerable population-weighted SVI quintiles, before the AC waste-heat penalty; the",
               "ratio is the median of the city ratios. SVI gain is the median change, in percentage points,",
               "in those quintiles' share of the benefit when units are ranked by SVI rather than by income",
               "(AC) or greenness deficit (trees). Saturated cities have no unit left to raise at that budget",
               "under the city's own GVI ceiling or under GVI 35."),
             label = "tab:svi_budget", longtable = TRUE, size = "\\footnotesize",
             align = c("l", "r", "r", "r", "r", "r", "r", "r", "r"),
             digits = c(0, 0, 2, 2, 0, 1, 1, 1, 0))
  invisible(list(fig = fig, tab = tab))
}

if (!exists(".MULTICITY_NORUN")) build_si6()
