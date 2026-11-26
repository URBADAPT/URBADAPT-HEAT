# =============================================================================
# fig3_synergies.R  --  Main Fig 3 (fig:result3)
# How the private lever (AC) and the public lever (greening) interact. This is
# the figure the paper's title is about, so every panel is an interaction: an
# effect one lever has on the other, or on itself.
#
#   (a) AC undermines itself: share of AC's gross mortality benefit taken back
#       by its own waste heat, against heat intensity. One point per city.
#   (b) Greening partly repairs it, but only in cool cities: share of the
#       waste-heat penalty removed when trees are deployed alongside AC.
#   (c) The two levers in the same units: deaths added by AC waste heat against
#       deaths avoided by the whole greening programme. Above the 1:1 line the
#       private lever adds more heat mortality than the public one removes.
#
# Every panel labels the cities that stand out, chosen by an explicit rule per
# panel (see the comment at each) and drawn with the shared repel_city() geom.
#
# Sources: <city>_cba_summary.json (benefits.ac.*, benefits.trees.*,
# vegetation_feedbacks.lambda_y_waste_heat.*).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

# Drawn at 11 x 9.5 in (a on top, b and c below) and printed at about 63% of
# that: base 14 pt prints axis titles at about 8.5 pt and ticks at about 7 pt.
FIG5_BASE <- 14
LABEL_SIZE <- 3.9   # city labels, about 7 pt in print

# Climate-class points at one uniform size: class fill with a dark rim, since
# mustard and snow blue are too light to read on white without one.
class_points <- function() list(
  geom_point(aes(fill = climate_cluster), shape = 21, colour = CLUSTER_OUTLINE,
             stroke = 0.3, size = 3.7, alpha = 0.9),
  scale_fill_manual(values = CLUSTER_COLORS, name = "Climate class", drop = FALSE,
                    guide = guide_legend(override.aes = list(size = 5))))

# All of a panel's cities, labelled only where chosen: ggrepel steers labels
# away from every point it is given, so the unlabelled cities go in with a
# blank label rather than being left out.
label_all <- function(d, top) {
  d$city_label <- ifelse(d$city %in% top$city, d$city_label, "")
  d
}

build_fig3 <- function(cities = discover_cities()) {
  banner("Main Fig 3: synergies and trade-offs between the levers")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  syn <- gather_cities(cities, read_cba)
  syn <- attach_meta(syn, meta)
  if (is.null(syn) || !nrow(syn)) { message("No CBA summaries."); return(invisible(NULL)) }

  # Ratios are only meaningful where the denominator is a real quantity, so a
  # city whose AC pathway avoids ~nothing (Dublin) drops out of (a)/(b) rather
  # than contributing a 0/0 point.
  syn$penalty_pct <- ifelse(syn$ac_gross > NO_BENEFIT_DEATHS,
                            100 * syn$ac_penalty / syn$ac_gross, NA_real_)
  syn$penalty_removed_pct <- ifelse(syn$ac_penalty > 1e-6,
                            100 * (1 - syn$penalty_trees / syn$ac_penalty), NA_real_)

  # --- (a) how much of AC's benefit its own waste heat takes back ------------
  a_dat <- syn[is.finite(syn$penalty_pct), , drop = FALSE]
  pa <- if (nrow(a_dat)) {
    lab <- ""
    if (nrow(a_dat) >= 3) {
      m <- lm(penalty_pct ~ warmseason_mean_t2m, data = a_dat)
      lab <- sprintf("+%.1f pp per °C  (R² = %.2f)",
                     coef(m)[2], summary(m)$r.squared)
    }
    # Two things "stand out" on a fitted scatter and they are not the same: the
    # extremes of the quantity itself, and the cities the trend fails to explain.
    # Label both, so the panel names the worst-affected cities and the ones that
    # sit furthest off the temperature relationship it is asserting.
    a_dat$resid <- if (nrow(a_dat) >= 3) resid(m) else NA_real_
    top <- rbind(standouts(a_dat, "penalty_pct", 3, 1),
                 standouts(a_dat, "resid", 1, 1))
    top <- top[!duplicated(top$city), , drop = FALSE]
    ggplot(a_dat, aes(warmseason_mean_t2m, penalty_pct)) +
      geom_smooth(method = "lm", formula = y ~ x, se = TRUE,
                  color = "grey45", fill = "grey88", linewidth = 0.8) +
      class_points() +
      repel_city(label_all(a_dat, top), size = LABEL_SIZE, point.size = 3.7) +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "a",
           x = "Warm-season mean air temperature (°C)",
           y = "Gross AC benefit taken back\nby its own waste heat") +
      theme_multicity(base_size = FIG5_BASE)
  } else patchwork::plot_spacer()

  # --- (b) how much of that greening repairs ---------------------------------
  b_dat <- syn[is.finite(syn$penalty_removed_pct), , drop = FALSE]
  pb <- if (nrow(b_dat)) {
    med <- median(b_dat$penalty_removed_pct, na.rm = TRUE)
    # This panel carried no labels at all, so the reader could see that greening
    # cancels almost none of the penalty but not where the exceptions are. Both
    # tails are worth naming: the few cities where greening does meaningful work,
    # and those where it does essentially none.
    #
    # Draw from the same restricted pool as panel c. A city with no usable
    # cooling coefficients cancels ~0% of the penalty by construction, so
    # labelling the low tail unfiltered names Madrid (0% coverage) and Sevilla
    # (8.6%) as the places greening fails -- which reports the gap in the
    # coefficient bridge as a finding about those cities. The points stay; only
    # the naming is restricted.
    b_pool <- b_dat[!is.na(b_dat$coef_lst_coverage_pct) &
                    b_dat$coef_lst_coverage_pct >= COEF_MIN_PCT, , drop = FALSE]
    top <- standouts(b_pool, "penalty_removed_pct", 3, 2)
    ggplot(b_dat, aes(warmseason_mean_t2m, penalty_removed_pct)) +
      geom_hline(yintercept = med, linetype = 2, color = "grey55") +
      class_points() +
      repel_city(label_all(b_dat, top), size = LABEL_SIZE, point.size = 3.7) +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "b",
           x = "Warm-season mean air temperature (°C)",
           y = "AC waste-heat penalty\ncancelled by greening") +
      theme_multicity(base_size = FIG5_BASE)
  } else patchwork::plot_spacer()

  # --- (c) does AC's waste heat outweigh the whole greening programme? -------
  # The two levers put in the same units: deaths added by the waste heat that
  # expanding air conditioning rejects outdoors, against deaths avoided by the
  # entire tree-planting programme. Above the 1:1 line the private lever adds
  # more heat mortality than the public one removes.
  #
  # This replaced a panel showing the share of the warning benefit that existing
  # AC already covers. That share is large (median 24%) but almost constant
  # (22.5-24.9% over an AC coverage range of 14-71%), so it drew as a flat band
  # and carried no cross-city information. It is stated in the text instead.
  #
  # Greening benefit is the confounded quantity here. With the shape aesthetic
  # gone there is no way to flag a sparse-coefficient city inside the panel, so
  # those cities are excluded rather than drawn as ordinary points: at 5%
  # coverage Barcelona returns a ratio of 17.6, which is an artefact of the
  # coefficient bridge and would read as a finding about Barcelona. The caption
  # states how many cities are left out.
  c_dat <- syn[is.finite(syn$ac_penalty) & syn$ac_penalty > 0 &
               is.finite(syn$trees_alone) & syn$trees_alone > NO_BENEFIT_DEATHS,
               , drop = FALSE]
  pc <- if (nrow(c_dat)) {
    n_sparse <- sum(is.na(c_dat$coef_lst_coverage_pct) |
                    c_dat$coef_lst_coverage_pct < COEF_MIN_PCT)
    wc <- c_dat[!is.na(c_dat$coef_lst_coverage_pct) &
                c_dat$coef_lst_coverage_pct >= COEF_MIN_PCT, , drop = FALSE]
    n_above <- sum(wc$ac_penalty > wc$trees_alone)
    # Rank by ratio, but only among cities where the absolute numbers matter:
    # Copenhagen's ratio of 1.7 is 1.1 deaths against 0.6 and would otherwise
    # be headlined alongside Athens' 369 against 83.
    lab_pool <- wc[wc$ac_penalty >= 5, , drop = FALSE]
    # Distance from the 1:1 line in log space, so BOTH tails get named. Ranking
    # on the raw ratio, as this did before, only ever surfaces cities above the
    # line; the ones where the greening programme comfortably outweighs the
    # waste heat are equally worth pointing at.
    lab_pool$log_ratio <- log10(lab_pool$ac_penalty / lab_pool$trees_alone)
    top <- standouts(lab_pool, "log_ratio", 3, 2)
    # Reference line at waste-heat deaths equal to a tenth of the deaths greening avoids.
    # Under the AC heat-flux method every drawn city sits well below 1:1, which left that
    # line in an empty corner; 1:10 runs through the cloud. Its label sits where the line
    # leaves the plotted range.
    ratios <- c(`1:10` = 0.1)
    xr <- range(wc$trees_alone); yr <- range(wc$ac_penalty)
    xr <- 10^(log10(xr) + c(-0.05, 0.05) * diff(log10(xr)))
    yr <- 10^(log10(yr) + c(-0.05, 0.05) * diff(log10(yr)))
    ref_lab <- do.call(rbind, lapply(names(ratios), function(nm) {
      r <- ratios[[nm]]
      # Near the lower end of the line, where it enters the panel, on its empty upper-left side:
      # at the upper end the label sat on the largest cities' points.
      x <- max(xr[1], yr[1] / r) * 10^(0.12 * diff(log10(xr)))
      data.frame(label = nm, x = x, y = r * x)
    }))
    ref_lab <- ref_lab[ref_lab$y >= yr[1] & ref_lab$x >= xr[1], , drop = FALSE]
    ggplot(wc, aes(trees_alone, ac_penalty)) +
      lapply(ratios, function(r) geom_abline(slope = 1, intercept = log10(r), linetype = 2,
                                             color = "grey55")) +
      geom_text(data = ref_lab, aes(x, y, label = label), inherit.aes = FALSE,
                hjust = 1.2, vjust = -0.3, size = LABEL_SIZE, color = "grey40") +
      class_points() +
      repel_city(label_all(wc, top), size = LABEL_SIZE, point.size = 3.7) +
      scale_x_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      scale_y_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      coord_cartesian(xlim = xr, ylim = yr, expand = FALSE) +
      labs(tag = "c",
           x = "Deaths avoided by greening\nover 25 years",
           y = "Deaths added by AC waste heat\nover 25 years") +
      theme_multicity(base_size = FIG5_BASE)
  } else patchwork::plot_spacer()

  # Caption numbers (panel titles/subtitles removed -- caption-only style), printed
  # so a rebuild reveals any drift against the LaTeX caption.
  # The waste-heat regression is no longer annotated on panel a, so its
  # coefficients are logged here instead -- the caption still quotes them. This
  # used to sit inside the panel-c block, so panel a's fit went unreported
  # whenever panel c had no data, and `lab` is undefined when panel a is empty.
  if (exists("lab") && nzchar(lab))
    message(sprintf("  [caption] panel a fit: %s", lab))

  if (nrow(c_dat)) {
    wc <- c_dat[c_dat$coef_lst_coverage_pct >= COEF_MIN_PCT &
                !is.na(c_dat$coef_lst_coverage_pct), , drop = FALSE]
    message(sprintf(paste0("  [caption] penalty cancelled by greening: median ",
                           "%.1f%%; AC waste heat outweighs the whole greening ",
                           "programme in %d of %d cities with usable greening ",
                           "coefficients (median ratio %.2f, max %.1f); %d city(ies) ",
                           "excluded for sparse greening coefficients"),
                    median(syn$penalty_removed_pct, na.rm = TRUE),
                    sum(wc$ac_penalty > wc$trees_alone), nrow(wc),
                    median(wc$ac_penalty / wc$trees_alone),
                    max(wc$ac_penalty / wc$trees_alone),
                    nrow(c_dat) - nrow(wc)))
  }

  # a centred on the top row at 72% of the width, b and c side by side below
  # (the user's layout): full width made the temperature scatter a thin strip.
  top <- patchwork::plot_spacer() + pa + patchwork::plot_spacer() +
    patchwork::plot_layout(widths = c(0.14, 0.72, 0.14))
  fig <- top / (pb | pc) +
    patchwork::plot_layout(heights = c(1.25, 1), guides = "collect") &
    # Legend text at the axis-title size rather than the smaller theme default.
    theme(legend.position = "bottom",
          legend.title = element_text(size = rel(1.1)),
          legend.text = element_text(size = rel(1)))
  save_item(fig, "fig3_synergies", width = 11, height = 9.5)
}

if (!exists(".MULTICITY_NORUN")) build_fig3()
