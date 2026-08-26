# =============================================================================
# fig1_risk_costs_portfolios.R  --  Main Fig 1 (fig:result1)
#
# Heat risk, cross-city adaptation outcome profiles, and public-private policy
# portfolios across the 40 European cities. Panels:
#
#   (a) Baseline annual heat mortality per 100k vs warm-season mean T2M.
#       Point size = population, colour = descriptive climate class.
#   (b) Standardised policy-outcome profiles, one row per city ordered by
#       descending baseline mortality, with climate class as an adjacent strip.
#       Oriented so that red is always the less favourable direction, blank where
#       a value is not a measurement. No city typology is imposed: see the note
#       in the body and the SI table tab_city_characteristics for the per-city
#       descriptors.
#   (c) Present-value cost per avoided death by pathway and climate class,
#       with the group medians annotated.
#   (d) Budget-constrained efficiency frontier, normalised per capita.
#   (e) Share of each lever deployed by the benefit-maximising portfolio as
#       available present-value budget per capita rises. AC is net of the
#       modelled waste-heat feedback throughout.
#
# Absorbs the whole of the former fig3_costeffectiveness, which overlapped it
# almost entirely: fig3a is panel c (now stratified by climate class rather than
# pooled), fig3b is panel d, and fig3c was already identical to this figure's
# deployment panel. fig3_costeffectiveness.R is therefore retired.
#
# fig1_risk_effectiveness, whose panel a was this panel a and whose pathway
# boxes duplicated columns of panel b, has been deleted.
# =============================================================================

.d <- {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  if (length(.f)) dirname(normalizePath(.f)) else getwd()
}
if (!exists("REPO_ROOT")) source(file.path(.d, "_helpers.R"))
# PROFILE_FEATURES / PROFILE_ANNOT / load_city_profiles() live in 01_city_profiles.R. Source
# it for the definitions only -- never let it rebuild the clustering from here.
if (!exists("PROFILE_FEATURES")) {
  .had_norun <- exists(".NATCITIES_NORUN")
  .NATCITIES_NORUN <- TRUE
  source(file.path(.d, "01_city_profiles.R"))
  if (!.had_norun) rm(.NATCITIES_NORUN)
}

# Diverging fill for the z-score heatmap, clipped so a single extreme city does
# not flatten the other 39 to indistinguishable white.
Z_LIMITS <- c(-2.5, 2.5)

# Frontier (panel d) y scale. Avoided deaths per 100k run from Dublin's ~0.6
# to Athens' ~400, so a natural scale is one city plus 39 near-flat lines on
# the floor; a log scale separates all 40 but flattens the diminishing-returns
# curvature. Set FALSE to compare.
FRONTIER_LOG_Y <- TRUE

# Rank-percentile within a column: puts four descriptors on very different units
# (%, %, %, effective class count) on one shared 0-1 grey scale.
.pctile <- function(x) {
  ok <- is.finite(x)
  out <- rep(NA_real_, length(x))
  if (sum(ok) > 1) out[ok] <- (rank(x[ok]) - 0.5) / sum(ok)
  else if (sum(ok) == 1) out[ok] <- 0.5
  out
}

build_fig1 <- function(cities = discover_cities()) {
  banner("Main Fig 1: risk, outcome profiles and portfolios")
  arch <- load_city_profiles()
  if (is.null(arch) || !nrow(arch)) {
    message("No city outcome profiles -- run 01_city_profiles.R first."); return(invisible(NULL)) }

  # One row per city, ordered by descending baseline mortality. No archetype
  # grouping: across four candidate variable sets the best mean silhouette was
  # 0.32, and the sets that broke free of the climate gradient were instead led
  # by greening-coefficient coverage, so no partition was defensible enough to
  # organise the panel by. The heatmap stands on its own as the cross-city
  # outcome profile.
  arch <- arch[order(-arch$mort_100k), , drop = FALSE]
  arch$city_f <- factor(arch$city_label, levels = rev(arch$city_label))

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
    theme_natcities()

  # --- (b) standardised outcome profiles + external annotations --------------
  zcols <- paste0("z_", PROFILE_FEATURES$key)
  zcols <- zcols[zcols %in% names(arch)]
  zl <- tidyr::pivot_longer(arch[, c("city_label", zcols)],
                            dplyr::all_of(zcols),
                            names_to = "key", values_to = "z")
  zl$key <- sub("^z_", "", zl$key)
  zl <- dplyr::left_join(
    zl, PROFILE_FEATURES[, c("key", "label", "group", "log", "worse_high")],
    by = "key")

  # Blank the cells that are not measurements. A cost per death is undefined
  # where the pathway avoids essentially no deaths (Madrid and Sevilla
  # greening); profile_matrix() censors those at the sample maximum so kmeans
  # could run, and the raw column is NA wherever that happened. Drawing the
  # imputed value as an ordinary dark cell would present a fabricated number as
  # data, so those tiles are left empty.
  raw <- tidyr::pivot_longer(arch[, c("city_label", PROFILE_FEATURES$key)],
                             dplyr::all_of(PROFILE_FEATURES$key),
                             names_to = "key", values_to = "raw")
  zl <- dplyr::left_join(zl, raw, by = c("city_label", "key"))
  n_blank <- sum(!is.finite(zl$raw))
  zl$z[!is.finite(zl$raw)] <- NA_real_

  # One convention for the whole panel: red = less favourable. Cost, risk and
  # burden variables already run that way; the benefit variables (% reduction,
  # avoided deaths) are sign-flipped so that a bigger benefit reads blue rather
  # than red. Without this the same colour means opposite things column to
  # column, which is worse than no colour coding at all.
  zl$z <- ifelse(zl$worse_high, zl$z, -zl$z)

  zl$city_f <- factor(zl$city_label, levels = levels(arch$city_f))
  zl$group <- factor(zl$group, levels = PROFILE_GROUP_LEVELS)
  # Variables share short labels across groups ("Trees" appears four times), so
  # order the x axis by the PROFILE_FEATURES row order within each group facet.
  zl$label <- factor(zl$label, levels = unique(PROFILE_FEATURES$label))


  b1 <- ggplot(zl, aes(label, city_f, fill = z)) +
    geom_tile(color = "white", linewidth = 0.25) +
    # A blank tile is white, and the diverging scale's midpoint is near-white
    # too, so an unmeasured cell would read as a merely average one. Mark them.
    geom_point(data = ~ dplyr::filter(.x, is.na(z)), shape = 4, size = 0.9,
               stroke = 0.35, colour = "grey45", show.legend = FALSE) +
    facet_grid(cols = vars(group), scales = "free_x", space = "free_x") +
    scale_fill_gradient2(low = "#1565C0", mid = "grey96", high = "#C62828",
                         midpoint = 0, limits = Z_LIMITS,
                         oob = scales::squish, na.value = "white",
                         name = "Oriented z-score",
                         breaks = c(-2, -1, 0, 1, 2),
                         guide = guide_colorbar(barheight = unit(0.35, "cm"),
                                                barwidth = unit(3.4, "cm"),
                                                title.position = "top")) +
    scale_x_discrete(position = "top") +
    labs(tag = "b", x = NULL, y = NULL) +
    theme_natcities() +
    theme(axis.text.y = element_text(size = rel(0.62)),
          axis.text.x.top = element_text(size = rel(0.66), angle = 45,
                                         hjust = 0, vjust = 0),
          strip.text.x = element_text(size = rel(0.72)),
          strip.text.y = element_blank(),
          panel.spacing = unit(3, "pt"),
          panel.grid = element_blank(),
          legend.position = "bottom")

  # Climate class: an external descriptor, drawn in the same palette as (a) so
  # the eye can read the profile against the climate gradient without a second legend.
  b2 <- ggplot(arch, aes(x = "Climate", y = city_f, fill = climate_cluster)) +
    geom_tile(color = "white", linewidth = 0.25) +
    scale_fill_manual(values = CLUSTER_COLORS, na.value = "grey90",
                      guide = "none", drop = FALSE) +
    scale_x_discrete(position = "top") +
    labs(x = NULL, y = NULL) +
    theme_natcities() +
    theme(axis.text.y = element_blank(),
          axis.text.x.top = element_text(size = rel(0.66), angle = 45,
                                         hjust = 0, vjust = 0))

  # The LCZ / coefficient-coverage descriptor strip that used to sit here was
  # removed: four more columns of grey on top of a 40-row heatmap was more
  # information than a main-text panel can carry. That per-city
  # characterisation is now tab_city_characteristics, an SI longtable.
  pb <- (b1 | b2) + patchwork::plot_layout(widths = c(1, 0.055))

  # --- (c) cost per avoided death by pathway and climate class --------------
  cea <- gather_cities(cities, function(c) {
    d <- read_cea(c)
    if (is.null(d)) return(NULL)
    d[, c("pathway", "cost_per_death", "no_benefit")]
  })
  cea <- attach_meta(cea, load_city_meta())
  cea <- cea[!is.na(cea$climate_cluster), , drop = FALSE]
  pc <- if (!is.null(cea) && nrow(cea)) {
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
      labs(tag = "c",
           x = "Climate class") +
      theme_natcities() +
      theme(legend.position = "bottom")
  } else patchwork::plot_spacer()

  # --- (d) deployment as the per-capita budget rises -------------------------
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
  # --- (d) normalised efficiency frontier (was fig3b) -----------------------
  pd <- if (nrow(bud)) {
    # Avoided deaths per 100k span Dublin's ~0.1 to Athens' ~400, so a linear
    # axis is one city and 39 flat lines on the floor. Zero-benefit grid points
    # (budgets too small to buy anything) cannot be drawn on a log axis.
    fr <- bud[is.finite(bud$benefit_100k) & bud$benefit_100k > 0 &
              is.finite(bud$spent_cap) & bud$spent_cap > 0, , drop = FALSE]
    n_zero <- nrow(bud) - nrow(fr)
    if (n_zero) message(sprintf("  [note] frontier: %d of %d city-budget points at zero benefit omitted (log axis)",
                                n_zero, nrow(bud)))
    ggplot(fr, aes(spent_cap, benefit_100k, group = city)) +
      geom_line(aes(color = climate_cluster), linewidth = 0.55, alpha = 0.55) +
      geom_point(aes(color = climate_cluster), size = 1.1, alpha = 0.7) +
      cluster_scale() +
      scale_x_log10(labels = scales::label_number(prefix = "€", big.mark = ",")) +
      # FRONTIER_LOG_Y toggles the y scale; see the note where it is defined.
      { if (FRONTIER_LOG_Y)
          scale_y_log10(labels = function(v)
            formatC(v, format = "fg", digits = 2, drop0trailing = TRUE))
        else scale_y_continuous() } +
      labs(tag = "d", x = "PV cost spent per capita (log)",
           y = if (FRONTIER_LOG_Y) "Avoided deaths / 100k (25y, log)"
               else "Avoided deaths / 100k (25y)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (e) deployment as the per-capita budget rises (was fig3c) ------------
  pe <- if (nrow(bud)) {
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
      labs(tag = "e",
           x = "Budget available per capita (log)", y = "% deployment") +
      theme_natcities() +
      theme(legend.position = "bottom")
  } else patchwork::plot_spacer()

  # Panel titles and subtitles are deliberately absent (Nature style: everything
  # descriptive lives in the caption). The numbers the caption quotes are printed
  # here so a rebuild on new data shows immediately when the caption has drifted.
  n_clip <- sum(abs(zl$z) > max(Z_LIMITS), na.rm = TRUE)
  message(sprintf(paste0("  [caption] %d standardised outcome variables; %d cities; ",
                         "fit %s; %d log-transformed variable(s); %d cell(s) blanked ",
                         "as not measured; %d cell(s) clipped at |z|=%.1f"),
                  length(zcols), nrow(arch), lab, sum(PROFILE_FEATURES$log),
                  n_blank, n_clip, max(Z_LIMITS)))

  top <- (pa | pb) + patchwork::plot_layout(widths = c(0.85, 2.15))
  bot <- (pc | pd | pe) + patchwork::plot_layout(widths = c(1.15, 1, 1.05))
  fig <- (top / bot) + patchwork::plot_layout(heights = c(1.75, 1))
  save_item(fig, "fig1_risk_costs_portfolios", width = 18/1.3, height = 13/1.3)
}

if (!exists(".NATCITIES_NORUN")) build_fig1()
