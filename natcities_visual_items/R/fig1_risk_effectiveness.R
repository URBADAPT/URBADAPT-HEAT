# =============================================================================
# fig1_risk_effectiveness.R  --  Summary Fig 1 (fig:result1)
# Cross-city, grouped by climate cluster, normalised. No per-city panels.
#
#   (a) Baseline heat mortality (per 100k, log) vs warm-season heat intensity
#       -- one point per city, colour = cluster, size = population
#   (b) Mortality reduction (% of baseline) by pathway -- box + city points
#   (c) Avoided deaths per 100k by pathway -- box + city points
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_fig1 <- function(cities = discover_cities()) {
  banner("Summary Fig 1: risk & effectiveness across the climate gradient")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- (a) baseline mortality per 100k vs heat intensity ---------------------
  base <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", c),
                       quiet = TRUE)
    if (is.null(d)) return(NULL)
    data.frame(deaths_2020 = d$deaths_overall[d$year == min(d$year)][1])
  })
  base <- attach_meta(base, meta)
  pa <- if (!is.null(base) && any(!is.na(base$pop_k))) {
    base$mort_100k <- per_100k(base$deaths_2020, base$pop_k)
    # Baseline mortality spans three orders of magnitude (Dublin ~0.02 to Athens
    # 168 per 100k) and rises multiplicatively with heat, so the relationship is
    # fitted and drawn in log space. The previous linear fit on a linear axis was
    # dragged by Athens and extrapolated to negative deaths at the cool end.
    fit <- base[is.finite(base$mort_100k) & base$mort_100k > 0, , drop = FALSE]
    lab <- ""
    if (nrow(fit) >= 3) {
      m <- lm(log10(mort_100k) ~ warmseason_mean_t2m, data = fit)
      # Slope in log10 units per degC -> multiplicative factor per degC.
      lab <- sprintf("x%.2f per °C  (R² = %.2f)",
                     10^coef(m)[2], summary(m)$r.squared)
    }
    ggplot(base, aes(warmseason_mean_t2m, mort_100k)) +
      { if (nrow(fit) >= 3)
          geom_smooth(data = fit, method = "lm", se = TRUE, color = "grey45",
                      fill = "grey85", linewidth = 0.7, formula = y ~ x) } +
      geom_point(aes(color = climate_cluster, size = pop_k), alpha = 0.85) +
      scale_size_continuous(range = c(1.6, 6), guide = "none") +
      scale_y_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      annotation_logticks(sides = "l", size = 0.3, colour = "grey70",
                          short = unit(0.05, "cm"), mid = unit(0.1, "cm"),
                          long = unit(0.15, "cm")) +
      cluster_scale() +
      labs(tag = "a", title = "Heat mortality rises steeply with heat",
           subtitle = lab,
           x = "Warm-season mean T2M (°C)",
           y = "Heat deaths / 100k / yr (log)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- pathway effectiveness ------------------------------------------------
  eff <- gather_cities(cities, canonical_effectiveness)
  eff <- attach_meta(eff, meta)

  pathway_box <- function(df, yvar, ylab, tag, title, subtitle = NULL,
                          log_y = FALSE) {
    df <- df[is.finite(df[[yvar]]), , drop = FALSE]
    if (log_y) df <- df[df[[yvar]] > 0, , drop = FALSE]
    df$pathway <- factor(df$pathway, levels = PATHWAY_LEVELS)
    ggplot(df, aes(pathway, .data[[yvar]])) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95",
                   color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.14, height = 0,
                  size = 2.2, alpha = 0.8) +
      cluster_scale() +
      # Two significant figures, never scientific: a fixed accuracy renders the
      # sub-0.01 end of this range (Sevilla greening) as a bare "0".
      { if (log_y) scale_y_log10(labels = function(v)
          formatC(v, format = "fg", digits = 2, drop0trailing = TRUE)) } +
      labs(tag = tag, title = title, subtitle = subtitle, x = NULL, y = ylab) +
      theme_natcities()
  }

  n_city <- if (!is.null(eff)) length(unique(eff$city)) else 0L
  sub_n <- sprintf("one point per city (n = %d); AC net of waste heat", n_city)

  pb <- if (!is.null(eff))
    pathway_box(eff, "pct_reduction", "% of baseline heat deaths", "b",
                "Mortality reduction by pathway", sub_n) else patchwork::plot_spacer()

  pc <- if (!is.null(eff) && any(!is.na(eff$pop_k))) {
    eff$avoided_100k <- per_100k(eff$avoided_deaths_25y, eff$pop_k)
    # Spans Dublin's 0.1 to Athens' 364 per 100k, so a linear axis is one
    # outlier and 119 points squashed on the floor.
    pathway_box(eff, "avoided_100k", "Avoided deaths / 100k (25y, log)", "c",
                "Avoided deaths by pathway", sub_n, log_y = TRUE)
  } else patchwork::plot_spacer()

  fig <- (pa | pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom",
          plot.subtitle = element_text(size = rel(0.82)))
  save_item(fig, "fig1_risk_effectiveness", width = 13, height = 5.1)
}

if (!exists(".NATCITIES_NORUN")) build_fig1()
