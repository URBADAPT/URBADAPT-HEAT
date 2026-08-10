# =============================================================================
# fig4_synergies.R  --  Summary Fig 4 (fig:result4)
# How the private lever (AC) and the public lever (greening) interact. This is
# the figure the paper's title is about, so every panel is an interaction: an
# effect one lever has on the other, or on itself.
#
#   (a) AC undermines itself: share of AC's gross mortality benefit taken back
#       by its own waste heat, against heat intensity. One point per city.
#   (b) Greening partly repairs it, but only in cool cities: share of the
#       waste-heat penalty removed when trees are deployed alongside AC.
#   (c) The two levers overlap: how much of greening's stand-alone benefit
#       survives once AC is already in place (people already cooled indoors
#       cannot be saved twice).
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

build_fig4 <- function(cities = discover_cities()) {
  banner("Summary Fig 4: synergies and trade-offs between the levers")
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
  syn$surviving_pct <- ifelse(syn$trees_alone > NO_BENEFIT_DEATHS,
                            100 * syn$trees_ontop / syn$trees_alone, NA_real_)

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
      labs(tag = "a", title = "Air conditioning eats its own benefit",
           subtitle = paste("Gross cooling benefit lost to AC's waste heat\n", lab),
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
      labs(tag = "b", title = "Greening offsets little of it",
           subtitle = sprintf(
             "Penalty cancelled by adding trees; median %.1f%%,\nand near zero exactly where the penalty is largest",
             med),
           x = "Warm-season mean T2M (°C)",
           y = "AC waste-heat penalty\ncancelled by greening") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) overlap between the two levers ------------------------------------
  c_dat <- syn[is.finite(syn$surviving_pct), , drop = FALSE]
  pc <- if (nrow(c_dat)) {
    med <- median(100 - c_dat$surviving_pct, na.rm = TRUE)
    ggplot(c_dat, aes(trees_alone, trees_ontop)) +
      geom_abline(slope = 1, intercept = 0, linetype = 2, color = "grey60") +
      geom_point(aes(color = climate_cluster), size = 2.6, alpha = 0.85) +
      cluster_scale() +
      scale_x_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      scale_y_log10(labels = scales::label_number(accuracy = 0.1, drop0trailing = TRUE)) +
      labs(tag = "c", title = "The levers overlap, but only mildly",
           subtitle = sprintf(
             "Greening loses a median %.1f%% of its benefit\nonce AC is already deployed", med),
           x = "Greening alone (avoided deaths, 25y, log)",
           y = "Greening on top of AC (log)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  fig <- (pa | pb | pc) + patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom",
          plot.subtitle = element_text(size = rel(0.78)))
  save_item(fig, "fig4_synergies", width = 14.5, height = 5.2)
}

if (!exists(".NATCITIES_NORUN")) build_fig4()
