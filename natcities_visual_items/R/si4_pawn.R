# =============================================================================
# si4_pawn.R  --  SI: which sampled parameters drive each outcome, across cities
#
# Median PAWN Kolmogorov-Smirnov statistic (ten conditioning intervals, N=128)
# for annual heat mortality and for the cost per avoided death of each lever.
# Each panel keeps the parameters with the highest cross-city median score; the
# box is the spread over the 40 cities, points are cities by climate class.
#
# Sources: tables/uncertainty_runs/<campaign>/sens_{aai_agg,cba_ac,cba_trees,
# cba_ews}_<city>_improved_fast.csv. The NB09 runs sit outside the slimmed
# OUTPUTS_BASE export, so the tree and campaign are given explicitly:
#   PAWN_TREE      variant dir holding <city>/tables/uncertainty_runs
#   PAWN_CAMPAIGN  campaign directory name or glob (one match per city)
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

PAWN_OUTCOMES <- list(
  list(file = "sens_aai_agg",   col = "aai_agg",                        title = "Annual heat mortality"),
  list(file = "sens_cba_ac",    col = "ac_cost_per_net_death_25y_cum",  title = "AC cost per avoided death"),
  list(file = "sens_cba_trees", col = "tree_cost_per_death_25y_cum",    title = "Tree cost per avoided death"),
  list(file = "sens_cba_ews",   col = "ews_cost_per_net_death_25y_cum", title = "EWS cost per avoided death"))

# Readable names for the coordinates that reach the top of any panel; anything
# else keeps its sampling-dictionary name (Supplementary Table of UQ dimensions).
PAWN_LABELS <- c(
  YEAR_IDX = "Target year", DISP_FRAC = "Mortality displacement",
  MDD_SCALE_65P = "65+ mortality-function scale", MDD_SCALE_15_64 = "15-64 mortality-function scale",
  PAA_SCALE = "Affected-exposure scale", EXP_TOTAL_SCALE = "Population scale",
  EXP_SSP_IDX = "Population scenario", IF_FAMILY_IDX = "Masselot extrapolation",
  CLIM_SCEN_IDX = "Climate scenario", CLIM_BAND_IDX = "Warming band", GCM_MODEL_IDX = "Climate model",
  BASELINE_MODE_IDX = "Baseline hazard mode", AC_EFF_SCEN_IDX = "AC efficacy scenario",
  AC_SSP_IDX = "AC penetration scenario", AC_CAPEX_MULT_IDX = "AC capital cost",
  AC_TARIFF_EUR_PER_KWH_IDX = "Electricity tariff", AC_LIFETIME_YEARS_IDX = "AC lifetime",
  WH_ENABLED_IDX = "Waste heat on/off", WH_RATIO = "Waste-heat day/night ratio",
  WH_LUT_CASE_IDX = "Waste-heat case", TREE_OM_MULT_IDX = "Tree O&M",
  TREE_CAP_UPLIFT = "Canopy target", TREE_CAPEX_MULT_IDX = "Tree capital cost",
  TREE_COEFF_SCALE = "Greening-cooling coefficient", TREE_RAMP_YEARS_IDX = "Tree maturity ramp",
  TREE_START_AGE_IDX = "Tree age at planting", DISCOUNT_RATE_IDX = "Discount rate",
  EWS_COST_MODEL_IDX = "EWS cost model", EWS_EFF_65P_LEVEL_IDX = "EWS efficacy, 65+",
  EWS_EFF_15_64_LEVEL_IDX = "EWS efficacy, 15-64", EWS_OVERLAP_LEVEL_IDX = "EWS-AC overlap",
  EWS_DISP_65P_LEVEL_IDX = "EWS displacement, 65+", EWS_RAMP_YEARS_IDX = "EWS ramp-up",
  EWS_INTERP_IDX = "EWS interpretation", EWS_TARGET_DAYS_IDX = "Warning-day target",
  EWS_RECALIB_YEARS_IDX = "Threshold recalibration", ELEC_COEFF_SCALE = "Tree-electricity feedback",
  ELEC_FEEDBACK_ENABLED_IDX = "Tree-electricity feedback on/off", COP_CASE_IDX = "AC efficiency loss case",
  COP_ENABLED_IDX = "AC efficiency loss on/off", CLIM_SOURCE_IDX = "Climate-delta source",
  MDD_SCALE_LT15 = "<15 mortality-function scale", IF_TREF_IDX = "Reference temperature",
  EWS_CF_EFF_LEVEL_IDX = "EWS efficacy, counterfactual", EWS_EFF_LT15_LEVEL_IDX = "EWS efficacy, <15",
  EWS_DISP_15_64_LEVEL_IDX = "EWS displacement, 15-64", EWS_DISP_LT15_LEVEL_IDX = "EWS displacement, <15")

pawn_label <- function(p) ifelse(p %in% names(PAWN_LABELS), PAWN_LABELS[p], p)

pawn_campaign_dir <- function(tree, city, pattern) {
  runs <- file.path(tree, city, "tables", "uncertainty_runs")
  hits <- if (dir.exists(runs)) list.dirs(runs, recursive = FALSE, full.names = TRUE) else character(0)
  hits <- hits[grepl(utils::glob2rx(pattern), basename(hits))]
  if (length(hits) != 1)
    stop(sprintf("%s: expected one campaign matching '%s', found %d", city, pattern, length(hits)))
  hits
}

build_si4 <- function(cities = discover_cities(), top_n = 8,
                      tree = Sys.getenv("PAWN_TREE"), campaign = Sys.getenv("PAWN_CAMPAIGN")) {
  banner("SI 4: PAWN sensitivity across cities")
  if (!nzchar(tree) || !nzchar(campaign)) {
    message("  PAWN_TREE / PAWN_CAMPAIGN not set -- skipping SI 4.")
    return(invisible(NULL))
  }
  meta <- load_city_meta()
  long <- dplyr::bind_rows(lapply(cities, function(city) {
    dir <- pawn_campaign_dir(tree, city, campaign)
    dplyr::bind_rows(lapply(PAWN_OUTCOMES, function(o) {
      d <- readr::read_csv(file.path(dir, sprintf("%s_%s_improved_fast.csv", o$file, city)),
                           show_col_types = FALSE)
      # NB09 writes no cost-per-death sensitivity where a lever avoids no deaths in
      # any member (Madrid's trees), so that city drops out of that panel only.
      if (!o$col %in% names(d)) return(NULL)
      d <- d[d$si == "median", c("param", o$col)]
      data.frame(city = city, outcome = o$title, param = d$param, ks = as.numeric(d[[o$col]]))
    }))
  }))
  long <- attach_meta(long, meta)
  long$outcome <- factor(long$outcome, levels = vapply(PAWN_OUTCOMES, `[[`, "", "title"))

  # The ten vulnerability coordinates do not enter mortality or costs, so their
  # scores are what a non-influential input reaches at N=128: the screening floor.
  panels <- lapply(levels(long$outcome), function(o) {
    d <- long[long$outcome == o & is.finite(long$ks), ]
    floor_ks <- median(d$ks[grepl("^VULN_", d$param)])
    d <- d[!grepl("^VULN_", d$param), ]
    top <- d |> dplyr::group_by(param) |> dplyr::summarise(m = median(ks), .groups = "drop") |>
      dplyr::slice_max(m, n = top_n, with_ties = FALSE)
    d <- d[d$param %in% top$param, ]
    d$label <- factor(pawn_label(d$param), levels = rev(pawn_label(top$param)))
    ggplot(d, aes(ks, label)) +
      geom_vline(xintercept = floor_ks, linetype = "dashed", color = "grey50") +
      geom_boxplot(outlier.shape = NA, fill = "grey92", color = "grey45", width = 0.6) +
      geom_point(aes(color = climate_cluster), size = 0.9, alpha = 0.65,
                 position = position_jitter(height = 0.15, width = 0, seed = 1)) +
      cluster_scale("Climate class") +
      labs(title = o, x = "Median PAWN KS statistic", y = NULL) +
      theme_natcities(10) + theme(panel.grid.major.y = element_blank())
  })
  p <- patchwork::wrap_plots(panels, ncol = 2, guides = "collect") +
    patchwork::plot_annotation(tag_levels = "a") &
    theme(legend.position = "bottom")
  save_item(p, "si4_pawn", width = 10, height = 8)
  readr::write_csv(long, file.path(TAB_DIR, "si4_pawn_long.csv"))
  invisible(long)
}

if (sys.nframe() == 0) build_si4()
