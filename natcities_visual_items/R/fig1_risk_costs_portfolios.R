# =============================================================================
# fig1_risk_costs_portfolios.R  --  Main Fig 1 (fig:result1)
#
# Heat risk, cross-city adaptation outcome profiles, and public-private policy
# portfolios across the 40 European cities. Panels:
#
#   (a) Baseline annual heat mortality per 100k vs warm-season mean T2M.
#       Point size = population, colour = descriptive climate class.
#   (b) Present-value cost per avoided death by pathway and climate class,
#       with the group medians annotated.
#   (c) Budget-constrained efficiency frontier, with both axes indexed to the
#       grid's smallest budget point so the shape of the returns is visible.
#   (d) Share of each lever deployed by the benefit-maximising portfolio as
#       available present-value budget per capita rises. AC is net of the
#       modelled waste-heat feedback throughout.
#
# The standardised outcome-profile heatmap that was panel b is now a figure of
# its own, fig1b_outcome_profiles.R -- 40 city rows by 15 columns had outgrown a
# panel slot. Panels c/d/e were retagged b/c/d to keep the sequence contiguous,
# so caption references to the old letters need updating.
#
# Absorbs the whole of the former fig3_costeffectiveness, which overlapped it
# almost entirely: fig3a is panel c (now stratified by climate class rather than
# pooled), fig3b is panel d, and fig3c was already identical to this figure's
# deployment panel. fig3_costeffectiveness.R is therefore retired.
#
# fig1_risk_effectiveness, whose panel a was this panel a and whose pathway
# boxes duplicated columns of the outcome-profile heatmap, has been deleted.
# =============================================================================

.d <- {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  if (length(.f)) dirname(normalizePath(.f)) else getwd()
}
if (!exists("REPO_ROOT")) source(file.path(.d, "_helpers.R"))
# load_city_profiles() lives in 01_city_profiles.R (it supplies mort_100k and the
# climate class for panel a). Source it for the definitions only -- never let it
# rebuild the clustering from here.
if (!exists("PROFILE_FEATURES")) {
  .had_norun <- exists(".NATCITIES_NORUN")
  .NATCITIES_NORUN <- TRUE
  source(file.path(.d, "01_city_profiles.R"))
  if (!.had_norun) rm(.NATCITIES_NORUN)
}

build_fig1 <- function(cities = discover_cities()) {
  banner("Main Fig 1: risk, cost-effectiveness and portfolios")
  arch <- load_city_profiles()
  if (is.null(arch) || !nrow(arch)) {
    message("No city outcome profiles -- run 01_city_profiles.R first."); return(invisible(NULL)) }

  arch <- arch[order(-arch$mort_100k), , drop = FALSE]

  # --- (a) baseline risk across the climate gradient -------------------------
  fit <- arch[is.finite(arch$mort_100k) & arch$mort_100k > 0 &
                is.finite(arch$warmseason_mean_t2m), , drop = FALSE]
  lab <- ""
  if (nrow(fit) >= 3) {
    m <- lm(log10(mort_100k) ~ warmseason_mean_t2m, data = fit)
    lab <- sprintf("x%.2f per °C  (R² = %.2f)", 10^coef(m)[2], summary(m)$r.squared)
  }
  pa <- ggplot(arch, aes(warmseason_mean_t2m, mort_100k)) +
    { if (nrow(fit) >= 3)
        geom_smooth(data = fit, method = "lm", se = TRUE, color = "grey45",
                    fill = "grey85", linewidth = 0.7, formula = y ~ x) } +
    geom_point(aes(color = climate_cluster, size = pop_k), alpha = 0.85) +
    scale_size_continuous(range = c(1.6, 6), guide = "none") +
    scale_y_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
    annotation_logticks(sides = "l", linewidth = 0.3, colour = "grey70",
                        short = unit(0.05, "cm"), mid = unit(0.1, "cm"),
                        long = unit(0.15, "cm")) +
    cluster_scale() +
    # The fitted slope stays inside the panel rather than in a subtitle: it is a
    # statistic OF this panel, and in the caption it would silently go stale the
    # next time the figure is rebuilt on new data.
    # annotate("text", x = -Inf, y = Inf, hjust = -0.08, vjust = 1.6, size = 3,
    #          color = "grey25", label = lab) +
    labs(tag = "a", x = "Warm-season mean T2M (°C)",
         y = "Heat deaths / 100k / yr (log)") +
    theme_natcities() +
    # a and c share the climate legend, b and d the pathway legend. They cannot
    # be merged by guides="collect" -- b draws box glyphs where d draws lines, so
    # patchwork keeps them as four distinct guides and they overflow the width.
    # Keep one of each pair, on the bottom row, so both sit at the figure's foot.
    theme(legend.position = "none")

  # The standardised outcome-profile heatmap that was panel b now lives in
  # fig1b_outcome_profiles.R, and takes its blanked-cell and clipping counts
  # with it.

  # --- (b) cost per avoided death by pathway and climate class --------------
  cea <- gather_cities(cities, function(c) {
    d <- read_cea(c)
    if (is.null(d)) return(NULL)
    d[, c("pathway", "cost_per_death", "no_benefit")]
  })
  cea <- attach_meta(cea, load_city_meta())
  cea <- cea[!is.na(cea$climate_cluster), , drop = FALSE]
  pb <- if (!is.null(cea) && nrow(cea)) {
    d <- cea[is.finite(cea$cost_per_death) & cea$cost_per_death > 0, , drop = FALSE]
    d$pathway <- factor(d$pathway, levels = PATHWAY_LEVELS)
    d$climate_cluster <- factor(d$climate_cluster, levels = CLUSTER_LEVELS)
    drop_n <- sum(cea$no_benefit, na.rm = TRUE)
    # fig3a annotated the pathway medians on the plot; keep that readout, but
    # pooled across cities and parked in the empty upper-left corner. Labelling
    # all nine class-by-pathway medians in situ was unreadable: three dodged
    # labels do not fit one class slot, rotated or not. The class stratification
    # is already legible from the boxes themselves.
    med <- d |>
      dplyr::group_by(pathway) |>
      dplyr::summarise(m = median(cost_per_death), .groups = "drop") |>
      dplyr::arrange(match(pathway, PATHWAY_LEVELS))
    med$lab <- sprintf("%s  %s", med$pathway,
      ifelse(med$m >= 1e6, paste0("€", round(med$m / 1e6, 1), "M"),
             paste0("€", round(med$m / 1e3), "k")))
    med$vj <- 1.6 + 1.5 * (seq_len(nrow(med)) - 1)
    # Per class-and-pathway medians, written on each box's median line. The
    # dodge offset has to be reconstructed by hand because geom_label has no
    # dodge of its own; a white fill with no border keeps the number readable
    # where it sits over the box and the jittered points.
    DODGE <- 0.78
    medg <- d |>
      dplyr::group_by(climate_cluster, pathway) |>
      dplyr::summarise(m = median(cost_per_death), .groups = "drop")
    medg$xpos <- as.numeric(medg$climate_cluster) +
      (as.numeric(medg$pathway) - 2) * DODGE / 3
    medg$lab <- ifelse(medg$m >= 1e6, paste0("€", round(medg$m / 1e6, 1), "M"),
                       paste0("€", round(medg$m / 1e3), "k"))
    ggplot(d, aes(climate_cluster, cost_per_death, color = pathway, fill = pathway)) +
      geom_boxplot(outlier.shape = NA, width = 0.7, alpha = 0.18,
                   position = position_dodge(width = 0.78),
                   linewidth = 0.45) +
      geom_point(position = position_jitterdodge(dodge.width = 0.78,
                                                 jitter.width = 0.16),
                 size = 1.5, alpha = 0.7) +
      # geom_text(data = med, inherit.aes = FALSE,
      #           aes(x = -Inf, y = Inf, label = lab, color = pathway, vjust = vj),
      #           hjust = -0.12, size = 2.5, show.legend = FALSE) +
      # geom_label(data = medg, inherit.aes = FALSE,
      #            aes(xpos, m, label = lab, colour = pathway),
      #            fill = "white", linewidth = 0,
      #            label.padding = unit(0.6, "pt"), label.r = unit(0, "pt"),
      #            size = 2, show.legend = FALSE) +
      scale_color_manual(values = PATHWAY_COLORS, name = "Adapt. policy") +
      scale_fill_manual(values = PATHWAY_COLORS, guide = "none") +
      eur_log_scale("Cost per death avoided (€, log)") +
      coord_cartesian(clip = "off") +
      labs(tag = "b",
           x = "Climate class") +
      theme_natcities() +
      theme(legend.position = "none")
  } else patchwork::plot_spacer()

  # --- (c) deployment as the per-capita budget rises -------------------------
  bud <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_budget_sensitivity.csv", c), quiet = TRUE)
    need <- c("budget", "max_cost", "max_benefit",
              "max_ben_trees", "max_ben_ac", "max_ben_ews")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d[, need]
  })
  bud <- attach_meta(bud, load_city_meta())
  bud <- bud[!is.na(bud$pop_k) & bud$pop_k > 0, , drop = FALSE]
  # Per-capita normalisation is what puts 40 cities of very different size on
  # shared axes: the budget grid is the same absolute 0.5-2.0 bn EUR everywhere.
  bud$budget_cap   <- bud$budget   / (bud$pop_k * 1000)
  bud$spent_cap    <- bud$max_cost / (bud$pop_k * 1000)
  bud$benefit_100k <- per_100k(bud$max_benefit, bud$pop_k)
  # --- (c) efficiency frontier, in per-capita terms --------------------------
  # Both axes are per-capita quantities, so the 40 cities are directly
  # comparable and overlap on the x axis:
  #   x = present-value cost actually spent per capita
  #   y = share of the city's own 25-year baseline heat-death burden avoided
  #
  # Dividing the benefit by that burden is what removes the level: in absolute
  # avoided deaths per 100k the cities span 788x, so no shared axis could show
  # them together (within a city, benefit rises by a median factor of only 1.26
  # across the whole grid). As a share of each city's own burden the spread is
  # ~20x and the curves sit on top of each other, which is the point.
  #
  # The denominator is the baseline series integrated over the benefit horizon,
  # NOT 25x the 2020 rate: baseline mortality nearly doubles to 2050 (Milan
  # 451 -> 725 deaths/yr), so the flat version understated the burden by ~25%
  # and put one city above 100% of its own burden avoided, which is what
  # exposed the error.
  #
  # Caveat that no rescaling can fix here: the budget grid is absolute
  # (0.5-2.0 bn EUR for every city, whatever its size), so each city is only
  # observed over its own per-capita window and none is observed below it.
  # Comparable axes, but not a comparable range. Only a per-capita grid upstream
  # would give that.
  BURDEN_YEARS <- 2020:2044   # span of the *_25y_* benefit tables

  baseline_burden <- function(city) {
    d <- read_city_csv(city, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv",
                                     city), quiet = TRUE)
    if (is.null(d) || !all(c("year", "deaths_overall") %in% names(d))) return(NA_real_)
    d <- d[is.finite(d$year) & is.finite(d$deaths_overall), , drop = FALSE]
    if (nrow(d) < 2) return(NA_real_)
    # The series is decadal (2020/2030/2040/2050); interpolate to annual and sum.
    sum(stats::approx(d$year, d$deaths_overall, xout = BURDEN_YEARS, rule = 2)$y)
  }

  pc <- if (nrow(bud)) {
    burden <- vapply(sort(unique(bud$city)), baseline_burden, numeric(1))
    bud$burden <- burden[bud$city]
    fr <- bud[is.finite(bud$benefit_100k) & bud$benefit_100k > 0 &
              is.finite(bud$spent_cap) & bud$spent_cap > 0 &
              is.finite(bud$burden) & bud$burden > 0, , drop = FALSE]
    n_drop <- dplyr::n_distinct(bud$city) - dplyr::n_distinct(fr$city)
    if (n_drop) message(sprintf("  [note] frontier: %d city(ies) dropped for no usable baseline burden",
                                n_drop))
    fr$share <- 100 * fr$max_benefit / fr$burden
    # The budget grid is the same absolute 0.5-2.0 bn EUR everywhere, so in most
    # cities the optimiser runs out of things to buy partway up it and the
    # remaining grid points repeat one identical (cost, benefit) pair -- up to
    # six times over, which drew as a blob on the end of every line. Keep the
    # first saturated point as the frontier's endpoint and drop the rest.
    fr <- fr |>
      dplyr::arrange(city, spent_cap) |>
      dplyr::group_by(city) |>
      dplyr::mutate(.same = !is.na(dplyr::lag(spent_cap)) &
                            abs(spent_cap - dplyr::lag(spent_cap)) < 1e-6 &
                            abs(max_benefit - dplyr::lag(max_benefit)) < 1e-9) |>
      dplyr::mutate(saturates = any(.same)) |>
      dplyr::filter(!.same) |>
      dplyr::ungroup()
    ends <- fr |>
      dplyr::group_by(city) |>
      dplyr::slice_max(spent_cap, n = 1, with_ties = FALSE) |>
      dplyr::ungroup()
    # Within-city elasticity of benefit to spend. On these log-log axes it is
    # simply the slope of each city's segment, so the panel already shows it and
    # needs no label; it is reported as a caption number below instead.
    el <- vapply(split(fr, fr$city), function(d) {
      if (nrow(d) < 3) return(NA_real_)
      unname(coef(lm(log(share) ~ log(spent_cap), data = d))[2])
    }, numeric(1))
    ggplot(fr, aes(spent_cap, share, group = city)) +
      geom_line(aes(color = climate_cluster), linewidth = 0.55, alpha = 0.6) +
      geom_point(aes(color = climate_cluster), size = 0.9, alpha = 0.55) +
      # Filled endpoint where the portfolio maxes out inside the grid, so a
      # curve that has stopped can be told from one merely truncated by it.
      geom_point(data = ends[ends$saturates, ], aes(color = climate_cluster),
                 size = 1.9, shape = 16) +
      cluster_scale() +
      scale_x_log10(labels = scales::label_number(prefix = "€", big.mark = ",")) +
      scale_y_log10(labels = scales::label_number(suffix = "%")) +
      labs(tag = "c", x = "PV cost spent per capita (log)",
           y = "% of 25y baseline heat deaths avoided (log)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (d) deployment as the per-capita budget rises (was fig3c) ------------
  pd <- if (nrow(bud)) {
    lc <- tidyr::pivot_longer(bud, c("max_ben_trees", "max_ben_ac", "max_ben_ews"),
                              names_to = "lever", values_to = "frac")
    lc$pathway <- factor(dplyr::recode(lc$lever, max_ben_trees = "Trees",
                                       max_ben_ac = "AC", max_ben_ews = "EWS"),
                         levels = PATHWAY_LEVELS)
    ggplot(lc, aes(budget_cap, 100 * frac, color = pathway, fill = pathway)) +
      geom_smooth(method = "loess", formula = y ~ x, span = 0.9,
                  linewidth = 0.9, alpha = 0.15) +
      geom_point(size = 0.6, alpha = 0.22) +
      scale_color_manual(values = PATHWAY_COLORS, name = "Adapt. policy") +
      scale_fill_manual(values = PATHWAY_COLORS, guide = "none") +
      scale_x_log10(labels = scales::label_number(prefix = "€", big.mark = ",")) +
      # Clip at draw time: a scale limit would drop the loess ribbon's
      # out-of-range rows rather than just not showing them.
      coord_cartesian(ylim = c(0, 100)) +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "d",
           x = "Budget available per capita (log)", y = "% deployment") +
      theme_natcities() +
      theme(legend.position = "bottom")
  } else patchwork::plot_spacer()

  # Panel titles and subtitles are deliberately absent (Nature style: everything
  # descriptive lives in the caption). The numbers the caption quotes are printed
  # here so a rebuild on new data shows immediately when the caption has drifted.
  message(sprintf("  [caption] %d cities; fit %s", nrow(arch), lab))
  if (exists("fr") && nrow(fr)) {
    tops <- fr |> dplyr::group_by(city, climate_cluster) |>
      dplyr::summarise(top = max(share), .groups = "drop")
    byc <- tapply(tops$top, tops$climate_cluster, median)
    message(sprintf(paste0("  [caption] panel c: spend EUR %.0f-%.0f per capita ",
                           "avoids %.0f-%.0f%% of the 25y baseline burden (median ",
                           "by class: %s); median within-city elasticity %.2f; ",
                           "%d of %d cities saturate inside the grid"),
                    min(fr$spent_cap), max(fr$spent_cap),
                    min(fr$share), max(fr$share),
                    paste(sprintf("%s %.0f%%", names(byc), byc), collapse = ", "),
                    median(el, na.rm = TRUE),
                    sum(ends$saturates), dplyr::n_distinct(fr$city)))
  }

  # Four panels of similar visual weight, so a 2x2 grid rather than the old
  # heatmap-dominated top row. In a 2x2 each legend would otherwise be drawn
  # twice (climate class under a and c, pathway under b and d), so collect them
  # into one shared strip.
  fig <- ((pa | pb) / (pc | pd)) +
    patchwork::plot_layout(heights = c(1, 1.1))
  save_item(fig, "fig1_risk_costs_portfolios", width = 11.5, height = 9)
}

if (!exists(".NATCITIES_NORUN")) build_fig1()
