# =============================================================================
# fig1_risk_effectiveness.R  --  Summary Fig 1 (fig:result1)
# Cross-city, grouped by climate cluster, normalised. No per-city panels.
#
#   (a) Baseline heat mortality (per 100k) vs warm-season heat intensity
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
    ggplot(base, aes(warmseason_mean_t2m, mort_100k)) +
      { if (sum(!is.na(base$mort_100k)) >= 3)
          geom_smooth(method = "lm", se = FALSE, color = "grey60",
                      linewidth = 0.7, formula = y ~ x) } +
      geom_point(aes(color = climate_cluster), size = 3, alpha = 0.85) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster",
                         drop = FALSE) +
      labs(tag = "a", title = "Baseline heat mortality vs heat intensity",
           x = "Warm-season mean T2M (°C)", y = "Heat deaths / 100k / yr") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- pathway effectiveness ------------------------------------------------
  keep <- c("Trees (uniform)", "AC (NET)", "EWS (marginal)")
  eff <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_policy_effectiveness.csv", c))
    if (is.null(d)) return(NULL)
    d <- d[d$policy %in% keep, c("policy", "avoided_deaths_25y", "pct_reduction")]
    d$pathway <- pathway_key(d$policy)
    d
  })
  eff <- attach_meta(eff, meta)

  pathway_box <- function(df, yvar, ylab, tag, title) {
    df$pathway <- factor(df$pathway, levels = c("Trees", "AC", "EWS"))
    ggplot(df, aes(pathway, .data[[yvar]])) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95",
                   color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.12, height = 0,
                  size = 2.4, alpha = 0.85) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster",
                         drop = FALSE) +
      labs(tag = tag, title = title, x = NULL, y = ylab) +
      theme_natcities()
  }

  pb <- if (!is.null(eff))
    pathway_box(eff, "pct_reduction", "% of baseline heat deaths", "b",
                "Mortality reduction by pathway") else patchwork::plot_spacer()

  pc <- if (!is.null(eff) && any(!is.na(eff$pop_k))) {
    eff$avoided_100k <- per_100k(eff$avoided_deaths_25y, eff$pop_k)
    pathway_box(eff, "avoided_100k", "Avoided deaths / 100k (25y)", "c",
                "Avoided deaths by pathway") } else patchwork::plot_spacer()

  fig <- (pa | pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom") &
    scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE,
                       guide = guide_legend(override.aes =
                         list(size = 3, alpha = 1, shape = 16, linetype = 0)))
  save_item(fig, "fig1_risk_effectiveness", width = 13, height = 4.8)
}

if (!exists(".NATCITIES_NORUN")) build_fig1()
