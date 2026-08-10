# =============================================================================
# fig2_distribution.R  --  Summary Fig 2 (fig:result2)
# Public vs private costs and the distributional / justice implications of the
# three levers, across cities grouped by climate cluster.
#
#   (a) Who pays: public vs private PV cost per capita by lever, faceted by
#       cluster. Trees & EWS are collective (public); AC is largely an
#       out-of-pocket private burden.
#   (b) Equity vs efficiency: what income-targeted AC roll-out buys (share of
#       the mortality benefit reaching the two most vulnerable quintiles) and
#       what it costs (total lives saved), one point per city.
#   (c) Greening progressivity: uniform vs equity-targeted slope of district
#       greening on vulnerability.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

# The two most vulnerable quintiles in
# interim/deaths_by_vulnerability_quintile_popweighted_<city>.csv.
VULN_TOP2 <- c("Q4", "Q5-High")

# Per city: the efficiency cost and the equity gain of targeting AC support by
# income instead of rolling it out uniformly. Summed over the reported horizon
# years (2020-2050) so a city is one point.
equity_efficiency <- function(city) {
  d <- read_city_csv(city,
        sprintf("deaths_by_vulnerability_quintile_popweighted_%s.csv", city),
        where = "interim", quiet = TRUE)
  need <- c("quintile", "avoided_uniform_vs_base", "avoided_income_vs_base")
  if (is.null(d) || !all(need %in% names(d))) return(NULL)
  top <- d$quintile %in% VULN_TOP2
  tot_u <- sum(d$avoided_uniform_vs_base, na.rm = TRUE)
  tot_i <- sum(d$avoided_income_vs_base,  na.rm = TRUE)
  if (!is.finite(tot_u) || !is.finite(tot_i) || tot_u <= 0 || tot_i <= 0) return(NULL)
  data.frame(
    avoided_uniform = tot_u,
    avoided_income  = tot_i,
    # >0 = targeting saves fewer lives overall; <0 = targeting is a free win.
    eff_loss_pct    = 100 * (1 - tot_i / tot_u),
    share_top2_uniform = 100 * sum(d$avoided_uniform_vs_base[top], na.rm = TRUE) / tot_u,
    share_top2_income  = 100 * sum(d$avoided_income_vs_base[top],  na.rm = TRUE) / tot_i)
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
    la$pathway <- factor(la$pathway, levels = PATHWAY_LEVELS)
    la$bearer <- factor(la$bearer, levels = c("Private", "Public"))
    la$climate_cluster <- factor(la$climate_cluster, levels = CLUSTER_LEVELS)
    tot <- la |> dplyr::group_by(climate_cluster, pathway) |>
      dplyr::summarise(t = sum(eur_cap), .groups = "drop")
    ggplot(la, aes(pathway, eur_cap, fill = bearer)) +
      geom_col(width = 0.7) +
      # EWS costs ~1 EUR/capita against AC's several hundred, so its bar is
      # invisible next to them; the value label is what makes it readable.
      geom_text(data = tot, inherit.aes = FALSE,
                aes(pathway, t, label = paste0("€", round(t))),
                vjust = -0.45, size = 2.9, color = "grey25") +
      facet_wrap(~climate_cluster, nrow = 1, drop = FALSE) +
      scale_fill_manual(values = BEARER_COLORS, name = "Cost borne by",
                        breaks = c("Public", "Private")) +
      scale_y_continuous(expand = expansion(mult = c(0, 0.14))) +
      labs(tag = "a", title = "Who pays for adaptation",
           subtitle = "Greening & warnings are collective; cooling is a private out-of-pocket burden",
           x = NULL, y = "PV cost per capita (€)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (b) equity vs efficiency of targeting AC support ----------------------
  eq <- gather_cities(cities, equity_efficiency)
  eq <- attach_meta(eq, meta)
  pb <- if (!is.null(eq) && nrow(eq)) {
    eq$equity_gain <- eq$share_top2_income - eq$share_top2_uniform
    med <- c(eff = median(eq$eff_loss_pct, na.rm = TRUE),
             gain = median(eq$equity_gain, na.rm = TRUE))
    ggplot(eq, aes(eff_loss_pct, equity_gain)) +
      geom_vline(xintercept = 0, linetype = 2, color = "grey60") +
      geom_hline(yintercept = 0, linetype = 2, color = "grey60") +
      geom_point(aes(color = climate_cluster), size = 2.8, alpha = 0.85) +
      cluster_scale() +
      annotate("text", x = -Inf, y = Inf, hjust = -0.05, vjust = 1.4, size = 2.9,
               color = "grey35", label = "more equitable\nand more effective") +
      labs(tag = "b", title = "Equity is cheap, not free",
           subtitle = sprintf(
             "Median: +%.0f pp of benefit to the 2 most vulnerable quintiles for %.0f%% of lives saved",
             med[["gain"]], med[["eff"]]),
           x = "Lives saved given up by targeting (% of uniform)",
           y = "Benefit shifted to most\nvulnerable quintiles (pp)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) greening progressivity slope --------------------------------------
  slopes <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_policy_comparison.csv", c), quiet = TRUE)
    if (is.null(d) || !all(c("SVI", "ΔGVI_uniform", "ΔGVI_equity") %in% names(d)))
      return(NULL)
    if (nrow(d) < 3) return(NULL)
    data.frame(uniform = coef(lm(d[["ΔGVI_uniform"]] ~ d$SVI))[2],
               equity  = coef(lm(d[["ΔGVI_equity"]]  ~ d$SVI))[2])
  })
  slopes <- attach_meta(slopes, meta)
  pc <- if (!is.null(slopes) && nrow(slopes)) {
    lim <- range(c(slopes$uniform, slopes$equity), na.rm = TRUE)
    n_below <- sum(slopes$equity < slopes$uniform, na.rm = TRUE)
    ggplot(slopes, aes(uniform, equity)) +
      geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey60") +
      geom_hline(yintercept = 0, linewidth = 0.3, color = "grey75") +
      geom_vline(xintercept = 0, linewidth = 0.3, color = "grey75") +
      geom_point(aes(color = climate_cluster), size = 2.8, alpha = 0.85) +
      cluster_scale() +
      # Equal limits rather than coord_equal(): a fixed aspect ratio makes
      # patchwork shrink this panel's width until its title is clipped. The
      # dashed line is still exactly y = x.
      coord_cartesian(xlim = lim, ylim = lim) +
      labs(tag = "c", title = "Uniform greening is already progressive",
           subtitle = sprintf("Equity targeting steepens it in only %d of %d cities",
             nrow(slopes) - n_below, nrow(slopes)),
           x = "Uniform allocation (ΔGVI per unit SVI)",
           y = "Equity-targeted allocation") +
      theme_natcities()
  } else patchwork::plot_spacer()

  bottom <- (pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom")
  fig <- pa / bottom +
    patchwork::plot_layout(heights = c(1, 1.15)) &
    theme(plot.subtitle = element_text(size = rel(0.8)))
  save_item(fig, "fig2_distribution", width = 13, height = 8.8)
}

if (!exists(".NATCITIES_NORUN")) build_fig2()
