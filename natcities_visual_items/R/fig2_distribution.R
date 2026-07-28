# =============================================================================
# fig2_distribution.R  --  Summary Fig 2 (fig:result2)
# Public vs private costs and the distributional / justice implications of the
# three levers, across cities grouped by climate cluster.
#
#   (a) Who pays: public vs private PV cost per capita by lever, faceted by
#       cluster. Trees & EWS are collective (public); AC is largely an
#       out-of-pocket private burden.
#   (b) Equity vs efficiency: income-targeted vs uniform AC roll-out. Below the
#       1:1 line, protecting the vulnerable first costs some total lives saved.
#   (c) Greening progressivity: slope of district greening on vulnerability
#       (>0 = cooling steered toward high-vulnerability districts).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_fig2 <- function(cities = discover_cities()) {
  banner("Summary Fig 2: public vs private costs and distributional justice")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- (a) public vs private cost per capita, by lever & cluster -------------
  costs <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c))
    if (is.null(d)) return(NULL)
    d$pathway <- pathway_key(d$policy)
    d[, c("pathway", "public_pv", "private_pv")]
  })
  costs <- attach_meta(costs, meta)
  costs <- costs[!is.na(costs$pop_k) & !is.na(costs$climate_cluster), , drop = FALSE]
  pa <- if (!is.null(costs) && nrow(costs)) {
    agg <- costs |>
      dplyr::group_by(climate_cluster, pathway) |>
      dplyr::summarise(cap = sum(pop_k) * 1000,
                       Public = sum(public_pv) / cap,
                       Private = sum(private_pv) / cap, .groups = "drop")
    la <- tidyr::pivot_longer(agg, c("Public", "Private"),
                              names_to = "bearer", values_to = "eur_cap")
    la$pathway <- factor(la$pathway, levels = c("Trees", "AC", "EWS"))
    la$bearer <- factor(la$bearer, levels = c("Private", "Public"))
    la$climate_cluster <- factor(la$climate_cluster, levels = CLUSTER_LEVELS)
    ggplot(la, aes(pathway, eur_cap, fill = bearer)) +
      geom_col(width = 0.7) +
      facet_wrap(~climate_cluster, nrow = 1, drop = FALSE) +
      scale_fill_manual(values = BEARER_COLORS, name = "Cost borne by",
                        breaks = c("Public", "Private")) +
      scale_y_continuous(expand = expansion(mult = c(0, 0.08))) +
      labs(tag = "a", title = "Who pays for adaptation",
           subtitle = "Greening & warnings are collective; cooling is a private out-of-pocket burden",
           x = NULL, y = "PV cost per capita (€)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (b) equity efficiency trade-off ---------------------------------------
  eq <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_avoided_deaths_summary.csv", c),
                       where = "root", quiet = TRUE)
    if (is.null(d)) return(NULL)
    agg <- aggregate(avoided_deaths ~ policy, data = d, FUN = sum)
    data.frame(uniform = agg$avoided_deaths[agg$policy == "uniform"],
               targeted = agg$avoided_deaths[agg$policy == "income-targeted"])
  })
  eq <- attach_meta(eq, meta)
  pb <- if (!is.null(eq) && nrow(eq)) {
    lim <- range(c(eq$uniform, eq$targeted), na.rm = TRUE)
    ggplot(eq, aes(uniform, targeted)) +
      geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey60") +
      geom_point(aes(color = climate_cluster), size = 2.8, alpha = 0.85) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE) +
      coord_equal(xlim = lim, ylim = lim) +
      labs(tag = "b", title = "Equity costs efficiency",
           subtitle = "Below 1:1 = fewer total lives saved",
           x = "Uniform roll-out (avoided deaths)", y = "Income-targeted") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) greening progressivity slope --------------------------------------
  slopes <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_policy_comparison.csv", c))
    if (is.null(d) || !all(c("SVI", "ΔGVI_uniform", "ΔGVI_equity") %in% names(d)))
      return(NULL)
    if (nrow(d) < 3) return(NULL)
    data.frame(
      allocation = c("Uniform", "Equity-targeted"),
      slope = c(coef(lm(d[["ΔGVI_uniform"]] ~ d$SVI))[2],
                coef(lm(d[["ΔGVI_equity"]]  ~ d$SVI))[2]))
  })
  slopes <- attach_meta(slopes, meta)
  pc <- if (!is.null(slopes) && nrow(slopes)) {
    slopes$allocation <- factor(slopes$allocation, levels = c("Uniform", "Equity-targeted"))
    ggplot(slopes, aes(allocation, slope)) +
      geom_hline(yintercept = 0, linetype = 2, color = "grey60") +
      geom_boxplot(outlier.shape = NA, width = 0.5, fill = "grey95", color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.1, height = 0,
                  size = 2.4, alpha = 0.85) +
      scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE) +
      labs(tag = "c", title = "Is greening progressive?",
           subtitle = "Slope of ΔGVI on district vulnerability (>0 = pro-poor)",
           x = NULL, y = "ΔGVI per unit SVI") +
      theme_natcities()
  } else patchwork::plot_spacer()

  bottom <- (pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom") &
    scale_color_manual(values = CLUSTER_COLORS, name = "Climate cluster", drop = FALSE,
                       guide = guide_legend(override.aes =
                         list(size = 3, alpha = 1, shape = 16, linetype = 0)))
  fig <- pa / bottom +
    patchwork::plot_layout(heights = c(1, 1)) &
    theme(plot.subtitle = element_text(size = rel(0.8)))
  save_item(fig, "fig2_distribution", width = 13, height = 8.4)
}

if (!exists(".NATCITIES_NORUN")) build_fig2()
