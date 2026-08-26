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
# Sources: <city>_cba_summary.json (benefits.ac.*, benefits.trees.*,
# vegetation_feedbacks.lambda_y_waste_heat.*).
# vegetation_feedbacks.lambda_y_waste_heat.*).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
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
    top <- a_dat[order(-a_dat$penalty_pct), ][1:3, ]
    ggplot(a_dat, aes(warmseason_mean_t2m, penalty_pct)) +
      geom_smooth(method = "lm", formula = y ~ x, se = TRUE,
                  color = "grey45", fill = "grey85", linewidth = 0.7) +
      geom_point(aes(color = climate_cluster, size = pop_k), alpha = 0.85) +
      ggrepel::geom_text_repel(data = top, aes(label = city_label), size = 2.8,
                               color = "grey25", seed = 1, min.segment.length = 0) +
      scale_size_continuous(range = c(1.6, 6), guide = "none") +
      cluster_scale() +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "a",
           x = "Warm-season mean T2M (°C)",
           y = "Gross AC benefit taken back\nby its own waste heat") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (b) how much of that greening repairs ---------------------------------
  b_dat <- syn[is.finite(syn$penalty_removed_pct), , drop = FALSE]
  pb <- if (nrow(b_dat)) {
    med <- median(b_dat$penalty_removed_pct, na.rm = TRUE)
    ggplot(b_dat, aes(warmseason_mean_t2m, penalty_removed_pct)) +
      geom_hline(yintercept = med, linetype = 2, color = "grey55") +
      geom_point(aes(color = climate_cluster, size = pop_k), alpha = 0.85) +
      scale_size_continuous(range = c(1.6, 6), guide = "none") +
      cluster_scale() +
      scale_y_continuous(labels = scales::label_number(suffix = "%")) +
      labs(tag = "b",
           x = "Warm-season mean T2M (°C)",
           y = "AC waste-heat penalty\ncancelled by greening") +
      theme_natcities()
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
    top <- lab_pool[order(-lab_pool$ac_penalty / lab_pool$trees_alone), ][
      seq_len(min(4, nrow(lab_pool))), ]
    ggplot(wc, aes(trees_alone, ac_penalty)) +
      geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey60") +
      geom_point(aes(color = climate_cluster, size = pop_k), alpha = 0.85) +
      ggrepel::geom_text_repel(data = top, aes(label = city_label), size = 2.8,
                               color = "grey25", seed = 1, min.segment.length = 0) +
      scale_size_continuous(range = c(1.6, 6), guide = "none") +
      cluster_scale() +
      scale_x_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      scale_y_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      labs(tag = "c",
           x = "Deaths avoided by greening (25y, log)",
           y = "Deaths added by AC waste heat
(25y, log)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # Caption numbers (panel titles/subtitles removed -- Nature style), printed
  # so a rebuild reveals any drift against the LaTeX caption.
  if ("penalty_removed_pct" %in% names(syn))
  # Caption numbers (panel titles/subtitles removed -- Nature style), printed
  # so a rebuild reveals any drift against the LaTeX caption.
  if (exists("c_dat") && nrow(c_dat)) {
    wc <- c_dat[c_dat$coef_lst_coverage_pct >= COEF_MIN_PCT &
                !is.na(c_dat$coef_lst_coverage_pct), , drop = FALSE]
  # The waste-heat regression is no longer annotated on panel a, so its
  # coefficients are logged here instead -- the caption still quotes them.
  if (nzchar(lab)) message(sprintf("  [caption] panel a fit: %s", lab))
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

  fig <- (pa | pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom")
  save_item(fig, "fig3_synergies", width = 14.5, height = 4.7)
}

if (!exists(".NATCITIES_NORUN")) build_fig3()
