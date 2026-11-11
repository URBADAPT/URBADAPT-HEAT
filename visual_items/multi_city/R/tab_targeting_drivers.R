# =============================================================================
# tab_targeting_drivers.R  --  what drives the two targeting results, city by city
#
# Reads TAB_DIR/targeting_drivers_ac.csv and targeting_drivers_greening.csv, written by
# visual_items/multi_city/targeting_drivers.py (AC from the NB12 zone tables, greening from the
# policy_comparison tables), and writes two SI tables:
#   tab_ac_targeting_drivers   decomposition of the equity-efficiency result of income-targeted AC
#   tab_greening_drivers       why the vulnerability-weighted greening policy rarely steepens the
#                              SVI gradient of the uniform catch-up
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

# Round numeric columns to their printed digits and drop negative zeros ("-0.0").
round_cols <- function(df, digits) {
  for (j in seq_along(df)) if (is.numeric(df[[j]])) {
    r <- round(df[[j]], digits[j + 1]); r[!is.na(r) & r == 0] <- 0; df[[j]] <- r }
  df
}

build_tab_targeting_drivers <- function() {
  banner("Tables: drivers of the targeting results")
  meta <- load_city_meta()
  ac_p <- file.path(TAB_DIR, "targeting_drivers_ac.csv")
  gr_p <- file.path(TAB_DIR, "targeting_drivers_greening.csv")

  if (file.exists(ac_p)) {
    ac <- attach_meta(readr::read_csv(ac_p, show_col_types = FALSE), meta)
    ac$city_label <- city_label(ac$city)
    ac <- ac[order(ac$climate_cluster, -ac$equity_gain_pp), ]
    out <- data.frame(
      City = ac$city_label,
      Cluster = as.character(ac$climate_cluster),
      `Users placed differently (%)` = ac$users_moved_pct,
      `Mortality gap (%)` = ac$mortality_gap_pct,
      `Change in deaths avoided (%)` = ac$efficiency_change_pct,
      `Temperature gap (°C)` = ac$temperature_gap_c,
      `Age 65+ gap (pp)` = ac$age65_gap_pp,
      `Vulnerability gap (pp)` = ac$vulnerability_gap_pp,
      `Equity gain (pp)` = ac$equity_gain_pp,
      `Income-SVI correlation` = ac$rho_income_svi,
      check.names = FALSE)
    dig <- c(0, 0, 0, 1, 1, 1, 2, 1, 1, 1, 2)
    out <- round_cols(out, dig)
    save_table(out, "tab_ac_targeting_drivers",
      caption = paste(
        "Drivers of the equity-efficiency result of income-targeted AC (Fig.~\\ref{fig:distribution}b), by city.",
        "Both policies add the same number of users, so the change in deaths avoided equals the share of",
        "added users that targeting places in different AC zones from the uniform roll-out times the mortality",
        "gap, the difference in deaths avoided per added user between the zones that gain users and those that",
        "lose them, relative to the uniform mean. The equity gain decomposes in the same way into the share of",
        "the benefit placed differently and the vulnerability gap, the difference between gaining and losing",
        "zones in the share of their AC-attenuable deaths that falls in the two most vulnerable SVI quintiles.",
        "The temperature and age gaps compare the gaining and losing zones by population-weighted summer",
        "(May-September) mean T2M and share of residents aged 65 and over. The income-SVI correlation is the",
        "population-weighted Spearman correlation across zones between income rank and SVI, positive where",
        "poorer zones are more vulnerable. Zone values are summed over the four anchor years, as in",
        "Fig.~\\ref{fig:distribution}b: the change in deaths avoided counts all residents and is reproduced",
        "exactly, and the equity gain counts the residents with an SVI value and is reproduced to within",
        "0.1 percentage points."),
      label = "tab:ac_targeting_drivers", longtable = TRUE, size = "\\scriptsize",
      align = c("l", "l", "l", rep("r", 8)),
      digits = dig,
      colnames_tex = c("City", "Cluster",
        "\\shortstack{Users placed\\\\differently (%)}", "\\shortstack{Mortality\\\\gap (%)}",
        "\\shortstack{Change in deaths\\\\avoided (%)}", "\\shortstack{Temperature\\\\gap (°C)}",
        "\\shortstack{Age 65+\\\\gap (pp)}", "\\shortstack{Vulnerability\\\\gap (pp)}",
        "\\shortstack{Equity\\\\gain (pp)}", "\\shortstack{Income-SVI\\\\correlation}"))
    message(sprintf("  AC: %d cities", nrow(out)))
  } else message("  no targeting_drivers_ac.csv")

  if (file.exists(gr_p)) {
    gr <- attach_meta(readr::read_csv(gr_p, show_col_types = FALSE), meta)
    gr$city_label <- city_label(gr$city)
    gr <- gr[order(gr$climate_cluster, -gr$rho_deficit_svi), ]
    out <- data.frame(
      City = gr$city_label,
      Cluster = as.character(gr$climate_cluster),
      Regions = gr$n_regions,
      `Below target` = gr$n_below_target,
      `Deficit-SVI correlation` = gr$rho_deficit_svi,
      `CV of deficit` = gr$cv_deficit,
      `CV of SVI` = gr$cv_svi,
      `Uniform progressivity` = gr$slope_uniform,
      `Weighted progressivity` = gr$slope_weighted,
      `Weighted more progressive` = ifelse(gr$weighted_steeper, "yes", "no"),
      `Predicted` = ifelse(gr$criterion_weighted_steeper, "yes", "no"),
      check.names = FALSE)
    dig <- c(0, 0, 0, 0, 0, 2, 2, 2, 1, 1, 0, 0)
    out <- round_cols(out, dig)
    save_table(out, "tab_greening_drivers",
      caption = paste(
        "Drivers of the progressivity of the greening policies (Fig.~\\ref{fig:distribution}c), by city.",
        "The uniform catch-up raises every greening region below the city's third-quartile GVI to that value,",
        "so its increment is the region's canopy deficit; the vulnerability-weighted policy spreads the same",
        "total over the same regions in proportion to their SVI. Progressivity is the additional GVI points",
        "a region receives per unit of SVI (Fig.~\\ref{fig:distribution}c). Uniform progressivity has the sign of",
        "the correlation between canopy deficit and SVI, and the weighted policy is predicted to be more",
        "progressive where that",
        "correlation times the coefficient of variation (CV) of the deficit is below the CV of SVI, which holds",
        "exactly when every region is below the target. CVs are across greening regions."),
      label = "tab:greening_drivers", longtable = TRUE, size = "\\scriptsize",
      align = c("l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "c", "c"),
      digits = dig,
      colnames_tex = c("City", "Cluster", "Regions", "\\shortstack{Below\\\\target}",
        "\\shortstack{Deficit-SVI\\\\correlation}", "\\shortstack{CV of\\\\deficit}", "\\shortstack{CV of\\\\SVI}",
        "\\shortstack{Uniform\\\\progressivity}", "\\shortstack{Weighted\\\\progressivity}",
        "\\shortstack{Weighted more\\\\progressive}", "Predicted"))
    message(sprintf("  greening: %d cities", nrow(out)))
  } else message("  no targeting_drivers_greening.csv")
  invisible(NULL)
}

if (!exists(".MULTICITY_NORUN")) build_tab_targeting_drivers()
