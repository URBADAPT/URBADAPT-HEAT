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
  # The efficiency axis counts every resident: NB05's city totals of avoided deaths
  # (all cells), summed over the same anchor years. The quintile file resolves only
  # the cells with an SVI value (a median 99.8 % of the AC response, 95-96 % in a few
  # cities), so it is used for the equity shares alone.
  all_u <- read_city_csv(city, sprintf("annual_heat_deaths_climada_avoided_AC_uniform_%s.csv", city),
                         where = "interim", quiet = TRUE)
  all_i <- read_city_csv(city, sprintf("annual_heat_deaths_climada_avoided_AC_%s.csv", city),
                         where = "interim", quiet = TRUE)
  yrs <- unique(d$year)
  if (is.null(all_u) || is.null(all_i) || !all(c("year", "overall") %in% names(all_u)) ||
      !all(c("year", "overall") %in% names(all_i))) return(NULL)
  all_tot_u <- sum(all_u$overall[all_u$year %in% yrs], na.rm = TRUE)
  all_tot_i <- sum(all_i$overall[all_i$year %in% yrs], na.rm = TRUE)
  if (!is.finite(all_tot_u) || all_tot_u <= 0) return(NULL)
  data.frame(
    avoided_uniform = all_tot_u,
    avoided_income  = all_tot_i,
    # >0 = targeting saves fewer lives overall; <0 = targeting is a free win.
    eff_loss_pct    = 100 * (1 - all_tot_i / all_tot_u),
    eff_loss_pct_svi_cells = 100 * (1 - tot_i / tot_u),
    share_top2_uniform = 100 * sum(d$avoided_uniform_vs_base[top], na.rm = TRUE) / tot_u,
    share_top2_income  = 100 * sum(d$avoided_income_vs_base[top],  na.rm = TRUE) / tot_i)
}

# Drawn at 13 x 8.8 in and printed at about half that width, so text and marks
# are sized up as in Fig. 1: base 15 pt prints at about 7.5 pt (axis titles)
# and 6 pt (tick labels).
FIG3_BASE <- 15

# Climate-class points: class fill with a dark rim, since mustard and snow
# blue are too light to read on white without one.
class_points <- function() list(
  geom_point(aes(fill = climate_cluster), shape = 21, colour = CLUSTER_OUTLINE,
             stroke = 0.35, size = 3.8, alpha = 0.9),
  scale_fill_manual(values = CLUSTER_COLORS, name = "Climate class", drop = FALSE,
                    guide = guide_legend(override.aes = list(size = 4.5))))

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
  # Each bar in its option colour: public costs solid, private costs in tints of
  # the same colour. Street trees and HHEWS are wholly public; AC shows its public
  # subsidy (exactly 20% of the capital cost in all 40 cities) as a solid segment
  # and the private capital, maintenance and electricity as three tints. The
  # tints cannot all sit dE >= 15 apart (closest pair 13.3), so segments are
  # separated by white gaps, outlined in the option colour and labelled directly
  # on the largest AC bar.
  tint <- function(hex, s) {
    v <- grDevices::col2rgb(hex)[, 1]
    grDevices::rgb(t(round(v * s + 255 * (1 - s))), maxColorValue = 255)
  }
  ac_col <- PATHWAY_COLORS[["AC"]]
  COST_COMPONENTS <- c("AC electricity (private)", "AC maintenance (private)",
                       "AC capital (private)", "AC subsidy (public)",
                       "Street trees (public)", "HHEWS (public)")
  COST_COLORS <- c("Street trees (public)"    = PATHWAY_COLORS[["Trees"]],
                   "HHEWS (public)"           = PATHWAY_COLORS[["EWS"]],
                   "AC subsidy (public)"      = ac_col,
                   "AC capital (private)"     = tint(ac_col, 0.68),
                   "AC maintenance (private)" = tint(ac_col, 0.38),
                   "AC electricity (private)" = tint(ac_col, 0.12))
  COST_LEGEND <- c("Street trees (public)", "HHEWS (public)", "AC subsidy (public)",
                   "AC capital (private)", "AC maintenance (private)",
                   "AC electricity (private)")

  costs <- gather_cities(cities, function(c) {
    pp <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c))
    j  <- read_city_json(c, sprintf("%s_cba_summary.json", c), quiet = TRUE)
    if (is.null(pp) || is.null(j)) return(NULL)
    pp$pathway <- pathway_key(pp$policy)
    pub <- pp[pp$pathway %in% c("Trees", "EWS"), c("pathway", "public_pv"), drop = FALSE]
    pub <- data.frame(pathway = pub$pathway, pv = pub$public_pv,
                      component = ifelse(pub$pathway == "Trees", "Street trees (public)",
                                         "HHEWS (public)"), stringsAsFactors = FALSE)
    sub <- n1(pp$public_pv[pp$pathway == "AC"])
    capex <- n1(j$costs$ac$pv_capex)
    ac <- data.frame(
      pathway   = "AC",
      component = c("AC subsidy (public)", "AC capital (private)",
                    "AC maintenance (private)", "AC electricity (private)"),
      pv = c(sub, capex - sub, n1(j$costs$ac$pv_maint), n1(j$costs$ac$pv_elec)),
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
    tot <- agg |> dplyr::group_by(climate_cluster, pathway) |>
      dplyr::summarise(t = sum(eur_cap), .groups = "drop")
    ggplot(agg, aes(pathway, eur_cap, fill = component)) +
      # White borders give the stacked segments a gap; the outline layer below
      # rings each whole bar in its option colour so the palest tint stays visible.
      geom_col(width = 0.7, colour = "white", linewidth = 0.6) +
      geom_col(data = tot, inherit.aes = FALSE, aes(pathway, t, colour = pathway),
               width = 0.7, fill = NA, linewidth = 0.5, show.legend = FALSE) +
      # EWS costs ~10 EUR/capita against AC's several hundred, so its bar is
      # barely visible next to them; the value label is what makes it readable.
      geom_text(data = tot, inherit.aes = FALSE,
                aes(pathway, t, label = paste0("€", round(t))),
                vjust = -0.45, size = 4, color = "grey25") +
      facet_wrap(~climate_cluster, nrow = 1, drop = FALSE) +
      # Payer is carried by solid vs tint (explained in the caption), so the
      # legend names only the option and the AC cost component.
      scale_fill_manual(values = COST_COLORS, breaks = COST_LEGEND, name = NULL,
                        labels = function(x) sub(" \\((public|private)\\)$", "", x),
                        guide = guide_legend(nrow = 2, byrow = TRUE)) +
      scale_colour_manual(values = PATHWAY_COLORS, guide = "none") +
      scale_x_discrete(labels = PATHWAY_LABELS) +
      scale_y_continuous(expand = expansion(mult = c(0, 0.14))) +
      labs(tag = "a", x = NULL, y = "Present-value cost per capita (€)") +
      theme_multicity(base_size = FIG3_BASE)
  } else patchwork::plot_spacer()

  # --- (b) equity vs efficiency of targeting AC support ----------------------
  eq <- gather_cities(cities, equity_efficiency)
  eq <- attach_meta(eq, meta)
  pb <- if (!is.null(eq) && nrow(eq)) {
    eq$equity_gain <- eq$share_top2_income - eq$share_top2_uniform
    med <- c(eff = median(eq$eff_loss_pct, na.rm = TRUE),
             gain = median(eq$equity_gain, na.rm = TRUE))
    # Plotted so that right = more effective and up = more equitable: the win-win
    # quadrant is the upper right. Each quadrant is labelled.
    eq$eff_change <- -eq$eff_loss_pct
    q_lab <- function(u, r) sprintf("%s equitable,\n%s effective", if (u) "more" else "less",
                                    if (r) "more" else "less")
    ggplot(eq, aes(eff_change, equity_gain)) +
      geom_vline(xintercept = 0, linetype = 2, color = "grey60") +
      geom_hline(yintercept = 0, linetype = 2, color = "grey60") +
      class_points() +
      # Room below zero for the two lower quadrants (one city sits just below the line).
      # Headroom above the data so the upper quadrant labels sit clear of points.
      expand_limits(y = c(-6, 44)) +
      annotate("text", x = Inf, y = Inf, hjust = 1.02, vjust = 1.3, size = 3.8, lineheight = 0.9,
               color = "grey40", fontface = "italic", label = q_lab(TRUE, TRUE)) +
      annotate("text", x = -Inf, y = Inf, hjust = -0.02, vjust = 1.3, size = 3.8, lineheight = 0.9,
               color = "grey40", fontface = "italic", label = q_lab(TRUE, FALSE)) +
      annotate("text", x = Inf, y = -Inf, hjust = 1.02, vjust = -0.35, size = 3.8, lineheight = 0.9,
               color = "grey40", fontface = "italic", label = q_lab(FALSE, TRUE)) +
      annotate("text", x = -Inf, y = -Inf, hjust = -0.02, vjust = -0.35, size = 3.8, lineheight = 0.9,
               color = "grey40", fontface = "italic", label = q_lab(FALSE, FALSE)) +
      labs(tag = "b",
           x = "Change in deaths avoided under targeting (% of uniform)",
           y = "Benefit shifted to most\nvulnerable quintiles (pp)") +
      theme_multicity(base_size = FIG3_BASE)
  } else blank_panel("b", paste("no city has deaths_by_vulnerability_quintile_",
                                "popweighted_<city>.csv (interim/)", sep = ""))

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
      class_points() +
      # Equal limits rather than coord_equal(): a fixed aspect ratio makes
      # patchwork shrink this panel's width until its title is clipped. The
      # dashed line is still exactly y = x.
      coord_cartesian(xlim = lim, ylim = lim) +
      labs(tag = "c",
           x = "Uniform catch-up (GVI points per unit SVI)",
           y = "Vulnerability weighting\n(GVI points per unit SVI)") +
      theme_multicity(base_size = FIG3_BASE)
  } else patchwork::plot_spacer()

  # Caption numbers (panel titles/subtitles removed -- caption-only style), printed
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

if (!exists(".MULTICITY_NORUN")) build_fig2()
