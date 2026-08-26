# =============================================================================
# fig2_distribution.R  --  Main Fig 2 (fig:result2)
# Public vs private costs and the distributional / justice implications of the
# three levers, across cities grouped by climate cluster.
#
#   (a) What each lever costs per capita, faceted by cluster. Trees & EWS are
#       wholly public in the model, so they are split public/private; AC's
#       public share is a single assumed subsidy rate, so AC is split by cost
#       component (capital, maintenance, electricity) instead.
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
  banner("Main Fig 2: public vs private costs and distributional justice")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- (a) cost per capita by lever, split the way each cost is really borne ---
  # Trees and EWS are wholly public in the model, so a public/private split is
  # the informative one for them. For AC it is not: the public share there is a
  # single assumed number -- 20% of capex is treated as subsidised, maintenance
  # and electricity entirely private -- so `public_share` reduces to
  # 0.20 x capex/total and describes the assumption rather than the city
  # (verified: the implied rate is exactly 0.20 in all 40). What does vary, and
  # what a household actually faces, is where the money goes: purchase,
  # maintenance, electricity. So AC is broken out by component instead.
  COST_COMPONENTS <- c("Private", "AC: electricity", "AC: maintenance",
                       "AC: capital", "Public")
  COST_COLORS <- c("Public"          = "#00695C",
                   "Private"         = "#C62828",
                   "AC: capital"     = "#0D47A1",
                   "AC: maintenance" = "#1976D2",
                   "AC: electricity" = "#90CAF9")

  costs <- gather_cities(cities, function(c) {
    pp <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c))
    j  <- read_city_json(c, sprintf("%s_cba_summary.json", c), quiet = TRUE)
    if (is.null(pp) || is.null(j)) return(NULL)
    pp$pathway <- pathway_key(pp$policy)
    pub <- pp[pp$pathway %in% c("Trees", "EWS"),
              c("pathway", "public_pv", "private_pv"), drop = FALSE]
    pub <- tidyr::pivot_longer(pub, c("public_pv", "private_pv"),
                               names_to = "component", values_to = "pv")
    pub$component <- ifelse(pub$component == "public_pv", "Public", "Private")
    ac <- data.frame(
      pathway   = "AC",
      component = c("AC: capital", "AC: maintenance", "AC: electricity"),
      pv = c(n1(j$costs$ac$pv_capex), n1(j$costs$ac$pv_maint),
             n1(j$costs$ac$pv_elec)),
      stringsAsFactors = FALSE)
    dplyr::bind_rows(pub, ac)
  })
  costs <- attach_meta(costs, meta)
  costs <- costs[!is.na(costs$pop_k) & !is.na(costs$climate_cluster), , drop = FALSE]
  pa <- if (!is.null(costs) && nrow(costs)) {
    # There are now several rows per city, so the per-capita denominator has to
    # be built from distinct cities -- summing pop_k over the long frame would
    # count each city two or three times.
    citypop <- unique(costs[, c("city", "climate_cluster", "pop_k")])
    popc <- citypop |> dplyr::group_by(climate_cluster) |>
      dplyr::summarise(cap = sum(pop_k) * 1000, .groups = "drop")
    agg <- costs |>
      dplyr::group_by(climate_cluster, pathway, component) |>
      dplyr::summarise(pv = sum(pv, na.rm = TRUE), .groups = "drop") |>
      dplyr::left_join(popc, by = "climate_cluster") |>
      dplyr::mutate(eur_cap = pv / cap) |>
      dplyr::filter(is.finite(eur_cap), eur_cap > 0)
    agg$pathway <- factor(agg$pathway, levels = PATHWAY_LEVELS)
    agg$climate_cluster <- factor(agg$climate_cluster, levels = CLUSTER_LEVELS)
    # Level order sets the stack: the first level is drawn on top, so capital
    # sits at the base of the AC bar and Public at the base of the others.
    agg$component <- factor(agg$component, levels = COST_COMPONENTS)
    present <- levels(droplevels(agg$component))
    tot <- agg |> dplyr::group_by(climate_cluster, pathway) |>
      dplyr::summarise(t = sum(eur_cap), .groups = "drop")
    ggplot(agg, aes(pathway, eur_cap, fill = component)) +
      geom_col(width = 0.7) +
      # EWS costs ~1 EUR/capita against AC's several hundred, so its bar is
      # invisible next to them; the value label is what makes it readable.
      geom_text(data = tot, inherit.aes = FALSE,
                aes(pathway, t, label = paste0("€", round(t))),
                vjust = -0.45, size = 2.9, color = "grey25") +
      facet_wrap(~climate_cluster, nrow = 1, drop = FALSE) +
      scale_fill_manual(values = COST_COLORS, name = "Cost component",
                        breaks = intersect(c("Public", "Private", "AC: capital",
                                             "AC: maintenance", "AC: electricity"),
                                           present)) +
      scale_y_continuous(expand = expansion(mult = c(0, 0.14))) +
      labs(tag = "a", x = NULL, y = "PV cost per capita (€)") +
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
      labs(tag = "b",
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
      labs(tag = "c",
           x = "Uniform allocation (ΔGVI per unit SVI)",
           y = "Equity-targeted allocation") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # Caption numbers (panel titles/subtitles removed -- Nature style), printed
  # so a rebuild reveals any drift against the LaTeX caption.
  if (exists("med") && exists("slopes"))
  message(sprintf(paste0("  [caption] targeting shifts a median +%.1f pp of ",
                         "benefit to the 2 most vulnerable quintiles for ",
                         "%.1f%% of lives saved; equity greening steepens ",
                         "the SVI slope in %d of %d cities"),
                  med[["gain"]], med[["eff"]],
                  nrow(slopes) - sum(slopes$equity < slopes$uniform, na.rm = TRUE),
                  nrow(slopes)))

  bottom <- (pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom")
  fig <- pa / bottom +
    patchwork::plot_layout(heights = c(1, 1.15))
  save_item(fig, "fig2_distribution", width = 13, height = 8.8)
}

if (!exists(".NATCITIES_NORUN")) build_fig2()
