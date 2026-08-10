# =============================================================================
# si3_ews.R  --  SI: the early-warning pathway in detail
# EWS is the cheapest lever in the main figures; this is what sits behind that.
#
#   (a) Warning load: warning days per season, 2020 -> 2050, by cluster. The
#       cost of running a warning system scales with how often it fires.
#   (b) Concentration of risk: share of annual heat deaths falling on warning
#       days -- the ceiling on what a perfect warning system could avert.
#   (c) Benefit ramp: net avoided deaths per 100k over the horizon, showing the
#       assumed behavioural take-up ramp reaching full effectiveness.
#
# Sources: <city>_ews_warning_days.csv, annual_heat_deaths_baseline_current_ac_
# <city>.csv, ews_benefits_25y_<city>.csv (all 40 cities).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_si3 <- function(cities = discover_cities()) {
  banner("SI 3: early-warning system detail")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- (a) warning days over time --------------------------------------------
  wd <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_ews_warning_days.csv", c), quiet = TRUE)
    if (is.null(d) || !all(c("year", "n_warning_days") %in% names(d))) return(NULL)
    d[, c("year", "n_warning_days")]
  })
  wd <- attach_meta(wd, meta)
  pa <- if (!is.null(wd) && nrow(wd)) {
    b <- wd |>
      dplyr::filter(!is.na(climate_cluster)) |>
      dplyr::group_by(climate_cluster, year) |>
      dplyr::summarise(med = median(n_warning_days),
                       lo = quantile(n_warning_days, 0.25),
                       hi = quantile(n_warning_days, 0.75), .groups = "drop")
    ggplot(b, aes(year, med, color = climate_cluster, fill = climate_cluster)) +
      geom_ribbon(aes(ymin = lo, ymax = hi), alpha = 0.16, color = NA) +
      geom_line(linewidth = 0.9) + geom_point(size = 1.8) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE) +
      scale_fill_manual(values = CLUSTER_COLORS, guide = "none", drop = FALSE) +
      labs(tag = "a", title = "The warning system fires more often",
           subtitle = "Median warning days per season, IQR shaded",
           x = NULL, y = "Warning days per season") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (b) share of deaths on warning days -----------------------------------
  fr <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", c),
                       quiet = TRUE)
    if (is.null(d) || !"frac_deaths_on_warning_days" %in% names(d)) return(NULL)
    d[, c("year", "frac_deaths_on_warning_days")]
  })
  fr <- attach_meta(fr, meta)
  pb <- if (!is.null(fr) && nrow(fr)) {
    d <- fr[is.finite(fr$frac_deaths_on_warning_days) & !is.na(fr$climate_cluster), ]
    d$year <- factor(d$year)
    ggplot(d, aes(year, 100 * frac_deaths_on_warning_days)) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95", color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.14, height = 0,
                  size = 1.8, alpha = 0.75) +
      cluster_scale() +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "b", title = "Risk concentrates on warned days",
           subtitle = "Ceiling on what a perfect warning system could avert",
           x = NULL, y = "Annual heat deaths on warning days") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) benefit ramp ------------------------------------------------------
  bn <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("ews_benefits_25y_%s.csv", c), quiet = TRUE)
    need <- c("year", "scenario", "net_avoided_deaths")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d[d$scenario %in% c("central", "low", "high"), need]
  })
  bn <- attach_meta(bn, meta)
  pc <- if (!is.null(bn) && nrow(bn)) {
    d <- bn[!is.na(bn$pop_k) & !is.na(bn$climate_cluster), ]
    d$per100k <- per_100k(d$net_avoided_deaths, d$pop_k)
    b <- d |>
      dplyr::group_by(climate_cluster, scenario, year) |>
      dplyr::summarise(med = median(per100k, na.rm = TRUE), .groups = "drop")
    b$scenario <- factor(b$scenario, levels = c("low", "central", "high"))
    ggplot(b, aes(year, med, color = climate_cluster, linetype = scenario)) +
      geom_line(linewidth = 0.85) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE) +
      scale_linetype_manual(values = c(low = 3, central = 1, high = 2),
                            name = "Take-up") +
      labs(tag = "c", title = "Benefits ramp with behavioural take-up",
           subtitle = "Median net avoided deaths per 100k per year",
           x = NULL, y = "Net avoided deaths / 100k / yr") +
      theme_natcities()
  } else patchwork::plot_spacer()

  fig <- (pa | pb | pc) &
    theme(legend.position = "bottom", legend.box = "vertical",
          legend.text = element_text(size = rel(0.8)),
          plot.subtitle = element_text(size = rel(0.8)))
  save_item(fig, "si3_ews", width = 14, height = 5.4)
}

if (!exists(".NATCITIES_NORUN")) build_si3()
