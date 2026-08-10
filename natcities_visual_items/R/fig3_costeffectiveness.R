# =============================================================================
# fig3_costeffectiveness.R  --  Summary Fig 3 (fig:result3)
# What each euro buys, and how a city should spend a constrained budget.
#
#   (a) Cost per death avoided by pathway -- 3 pathways x 40 cities, log euros.
#       Warnings are two to three orders of magnitude cheaper than either
#       physical lever.
#   (b) Budget-constrained efficiency frontier, normalised per capita so 40
#       cities of very different size share one pair of axes.
#   (c) Deployment order: how far each lever is rolled out as the per-capita
#       budget rises -- which lever a city buys first, second, last.
#
# Sources: <city>_cea_summary.csv (uniform policy labels across all 40 cities)
# and <city>_budget_sensitivity.csv (8-point budget grid, benefit-maximising
# allocation at each level).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_fig3 <- function(cities = discover_cities()) {
  banner("Summary Fig 3: cost-effectiveness and budget choice")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- (a) cost per death avoided, by pathway --------------------------------
  cea <- gather_cities(cities, function(c) {
    d <- read_cea(c)
    if (is.null(d)) return(NULL)
    d[, c("pathway", "Policy", "PV_Cost_EUR", "Avoided_Deaths_25y",
          "cost_per_death", "no_benefit")]
  })
  cea <- attach_meta(cea, meta)
  pa <- if (!is.null(cea) && nrow(cea)) {
    d <- cea[!is.na(cea$cost_per_death) & cea$cost_per_death > 0, , drop = FALSE]
    d$pathway <- factor(d$pathway, levels = PATHWAY_LEVELS)
    drop_n <- sum(cea$no_benefit, na.rm = TRUE)
    med <- d |> dplyr::group_by(pathway) |>
      dplyr::summarise(m = median(cost_per_death), .groups = "drop")
    ggplot(d, aes(pathway, cost_per_death)) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95",
                   color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.14, height = 0,
                  size = 2.2, alpha = 0.8) +
      geom_text(data = med, inherit.aes = FALSE,
                aes(pathway, m, label = paste0("€", ifelse(m >= 1e6,
                      paste0(round(m / 1e6, 1), "M"), paste0(round(m / 1e3), "k")))),
                nudge_x = 0.42, size = 2.9, color = "grey20") +
      cluster_scale() +
      eur_log_scale("Cost per death avoided (€, log)") +
      labs(tag = "a", title = "Warnings buy lives 100x cheaper",
           subtitle = sprintf("Median per pathway; %s",
             if (drop_n) sprintf("%d city-pathway(s) with no measurable benefit omitted", drop_n)
             else "one point per city"),
           x = NULL) +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- budget grid, normalised per capita ------------------------------------
  bud <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_budget_sensitivity.csv", c), quiet = TRUE)
    need <- c("budget", "max_cost", "max_benefit",
              "max_ben_trees", "max_ben_ac", "max_ben_ews")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d[, need]
  })
  bud <- attach_meta(bud, meta)
  bud <- bud[!is.na(bud$pop_k) & bud$pop_k > 0, , drop = FALSE]
  # The budget grid is the same absolute 0.5-2.0 bn EUR in every city, so on a
  # per-capita basis it means something very different in Berlin (3.7 M people)
  # than in Ljubljana (0.27 M). Normalising is what makes the 40 cities
  # comparable on shared axes.
  bud$budget_cap <- bud$budget   / (bud$pop_k * 1000)
  bud$spent_cap  <- bud$max_cost / (bud$pop_k * 1000)
  bud$benefit_100k <- per_100k(bud$max_benefit, bud$pop_k)

  # --- (b) normalised efficiency frontier ------------------------------------
  pb <- if (nrow(bud)) {
    ggplot(bud, aes(spent_cap, benefit_100k, group = city)) +
      geom_line(aes(color = climate_cluster), linewidth = 0.55, alpha = 0.55) +
      geom_point(aes(color = climate_cluster), size = 1.1, alpha = 0.7) +
      cluster_scale() +
      scale_x_log10(labels = scales::label_number(prefix = "€", big.mark = ",")) +
      labs(tag = "b", title = "What a city gets for its money",
           subtitle = "Benefit-maximising portfolio at each budget level; flat = lever exhausted",
           x = "PV cost actually spent per capita (log)",
           y = "Avoided deaths / 100k (25y)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) deployment order as the budget rises ------------------------------
  pc <- if (nrow(bud)) {
    lc <- tidyr::pivot_longer(bud, c("max_ben_trees", "max_ben_ac", "max_ben_ews"),
                              names_to = "lever", values_to = "frac")
    lc$pathway <- factor(dplyr::recode(lc$lever,
                            max_ben_trees = "Trees", max_ben_ac = "AC",
                            max_ben_ews = "EWS"), levels = PATHWAY_LEVELS)
    ggplot(lc, aes(budget_cap, 100 * frac, color = pathway, fill = pathway)) +
      geom_smooth(method = "loess", formula = y ~ x, span = 0.9,
                  linewidth = 0.9, alpha = 0.15) +
      geom_point(size = 0.7, alpha = 0.28) +
      scale_color_manual(values = PATHWAY_COLORS, name = "Lever") +
      scale_fill_manual(values = PATHWAY_COLORS, guide = "none") +
      scale_x_log10(labels = scales::label_number(prefix = "€", big.mark = ",")) +
      # Clip at draw time, not in the scale: a scale limit would drop the
      # loess ribbon's out-of-range rows instead of just not showing them.
      coord_cartesian(ylim = c(0, 100)) +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "c", title = "Warnings first, cooling last",
           subtitle = "Share of each lever deployed by the benefit-maximising portfolio",
           x = "Budget available per capita (log)", y = "Lever deployed") +
      theme_natcities() +
      theme(legend.position = "bottom")
  } else patchwork::plot_spacer()

  # (c) keys on lever, not cluster, so the three panels cannot share one
  # collected guide. (a) and (b) show the same cluster scale, so the legend is
  # drawn once under (b) and dropped from (a).
  # guides(), not theme(): the trailing `&` below applies its theme to every
  # panel and would put the dropped legend straight back on (a).
  fig <- (pa + guides(color = "none") | pb | pc) +
    patchwork::plot_layout(guides = "keep", widths = c(1, 1.15, 1.15)) &
    theme(legend.position = "bottom",
          plot.subtitle = element_text(size = rel(0.8)))
  save_item(fig, "fig3_costeffectiveness", width = 15, height = 5.6)
}

if (!exists(".NATCITIES_NORUN")) build_fig3()
