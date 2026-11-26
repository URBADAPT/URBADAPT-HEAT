# =============================================================================
# fig1b_outcome_profiles.R  --  standalone: cross-city outcome profiles
#
# The standardised policy-outcome heatmap that used to be panel b of
# fig1_risk_costs_portfolios. Promoted to a figure of its own: at 40 city rows
# by 15 outcome columns it was carrying most of fig1's area and still rendering
# its row labels at 62% size, which is the point at which a panel has outgrown
# its figure.
#
# One row per city, ordered by descending baseline mortality, with climate class
# as an adjacent strip. Oriented so that red is always the less favourable
# direction, blank where a value is not a measurement. No city typology is
# imposed -- see the note in the body and the SI table tab_city_characteristics
# for the per-city descriptors.
#
# Kept as `fig1b_*` rather than renumbered into the fig2/fig3/fig4 sequence, so
# that its provenance stays obvious and the other figures keep their slots. The
# manuscript decides the final number.
# =============================================================================

.d <- {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  if (length(.f)) dirname(normalizePath(.f)) else getwd()
}
if (!exists("REPO_ROOT")) source(file.path(.d, "_helpers.R"))
# PROFILE_FEATURES / PROFILE_GROUP_LEVELS / load_city_profiles() live in
# 01_city_profiles.R. Source it for the definitions only -- never let it rebuild
# the clustering from here.
if (!exists("PROFILE_FEATURES")) {
  .had_norun <- exists(".MULTICITY_NORUN")
  .MULTICITY_NORUN <- TRUE
  source(file.path(.d, "01_city_profiles.R"))
  if (!.had_norun) rm(.MULTICITY_NORUN)
}

# Diverging fill for the z-score heatmap, clipped so a single extreme city does
# not flatten the other 39 to indistinguishable white.
Z_LIMITS <- c(-2.5, 2.5)

build_fig1b <- function(cities = discover_cities()) {
  banner("Fig 1b (standalone): cross-city outcome profiles")
  arch <- load_city_profiles()
  if (is.null(arch) || !nrow(arch)) {
    message("No city outcome profiles -- run 01_city_profiles.R first.")
    return(invisible(NULL))
  }

  # One row per city, ordered by descending baseline mortality. No archetype
  # grouping: across four candidate variable sets the best mean silhouette was
  # 0.32, and the sets that broke free of the climate gradient were instead led
  # by greening-coefficient coverage, so no partition was defensible enough to
  # organise the rows by. The heatmap stands on its own as the cross-city
  # outcome profile.
  arch <- arch[order(-arch$mort_100k), , drop = FALSE]
  arch$city_f <- factor(arch$city_label, levels = rev(arch$city_label))

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

  # One convention for the whole figure: red = less favourable. Cost, risk and
  # burden variables already run that way; the benefit variables (% reduction,
  # avoided deaths) are sign-flipped so that a bigger benefit reads blue rather
  # than red. Without this the same colour means opposite things column to
  # column, which is worse than no colour coding at all.
  zl$z <- ifelse(zl$worse_high, zl$z, -zl$z)

  zl$city_f <- factor(zl$city_label, levels = levels(arch$city_f))
  # Display wording follows the manuscript; the PROFILE_FEATURES labels stay as
  # they are because 01_city_profiles.R uses them too. Long group titles wrap
  # onto two lines rather than shrinking.
  DISPLAY_GROUP <- c("Risk" = "Risk",
                     "% reduction" = "Heat deaths\navoided (%)",
                     "Avoided/100k" = "Deaths avoided\nper 100,000",
                     "Cost/capita" = "Cost per\ncapita",
                     "Cost/death" = "Cost per\navoided death",
                     "Cost share & offset" = "Private share and\nwaste-heat offset")
  DISPLAY_LABEL <- c("Baseline deaths" = "Heat deaths per 100,000",
                     "Trees" = "Street trees", "AC" = "AC", "EWS" = "HHEWS",
                     "Private cost share" = "Private cost share",
                     "AC waste heat" = "AC waste-heat offset")
  zl$group <- factor(unname(DISPLAY_GROUP[as.character(zl$group)]),
                     levels = unname(DISPLAY_GROUP[PROFILE_GROUP_LEVELS]))
  # Variables share short labels across groups ("Trees" appears four times), so
  # order the x axis by the PROFILE_FEATURES row order within each group facet.
  lv <- unique(PROFILE_FEATURES$label)
  zl$label <- factor(unname(DISPLAY_LABEL[as.character(zl$label)]),
                     levels = unname(DISPLAY_LABEL[lv]))

  # Standing alone, the figure has the room the panel never had: city names and
  # column headers go back to full size and the tiles stop being cramped.
  b1 <- ggplot(zl, aes(label, city_f, fill = z)) +
    geom_tile(color = "white", linewidth = 0.25) +
    # A blank tile is white, and the diverging scale's midpoint is near-white
    # too, so an unmeasured cell would read as a merely average one. Mark them.
    geom_point(data = ~ dplyr::filter(.x, is.na(z)), shape = 4, size = 1.2,
               stroke = 0.4, colour = "grey45", show.legend = FALSE) +
    facet_grid(cols = vars(group), scales = "free_x", space = "free_x") +
    scale_fill_gradient2(low = DIVERGING_BLUE, mid = DIVERGING_MID, high = DIVERGING_RED,
                         midpoint = 0, limits = Z_LIMITS,
                         oob = scales::squish, na.value = "white",
                         name = "Oriented z-score",
                         # End labels at the saturation points say which way is
                         # unfavourable, so the figure reads without the caption.
                         breaks = c(-2.5, -1, 0, 1, 2.5),
                         labels = c("-2.5\nMore favourable", "-1", "0", "1",
                                    "2.5\nLess favourable"),
                         guide = guide_colorbar(barheight = unit(0.35, "cm"),
                                                barwidth = unit(6.5, "cm"),
                                                title.position = "top")) +
    scale_x_discrete(position = "top") +
    labs(x = NULL, y = NULL) +
    theme_multicity() +
    # City names sit on the climate strip to the left, so none here.
    theme(axis.text.y = element_blank(),
          axis.text.x.top = element_text(size = rel(0.85), angle = 45,
                                         hjust = 0, vjust = 0),
          strip.text.x = element_text(size = rel(0.9), lineheight = 0.9),
          strip.text.y = element_blank(),
          # Group titles above the rotated column headers, not between them and
          # the tiles; the right margin keeps the last rotated header inside.
          strip.placement = "outside",
          plot.margin = margin(8, 80, 8, 2),
          panel.spacing = unit(3, "pt"),
          panel.grid = element_blank(),
          legend.position = "bottom")

  # Climate class: an external descriptor, drawn in the shared class palette
  # between the city names and the heatmap, with its own legend.
  b2 <- ggplot(arch, aes(x = "Climate class", y = city_f, fill = climate_cluster)) +
    geom_tile(color = "white", linewidth = 0.25) +
    scale_fill_manual(values = CLUSTER_COLORS, na.value = "grey90", drop = FALSE,
                      name = "Climate class",
                      guide = guide_legend(title.position = "top", nrow = 1)) +
    scale_x_discrete(position = "top") +
    labs(x = NULL, y = NULL) +
    theme_multicity() +
    theme(axis.text.y = element_text(size = rel(0.85)),
          axis.text.x.top = element_text(size = rel(0.85), angle = 45,
                                         hjust = 0, vjust = 0),
          panel.grid = element_blank(),
          legend.position = "bottom")

  # The LCZ / coefficient-coverage descriptor strip that used to sit here was
  # removed: four more columns of grey on top of a 40-row heatmap was more
  # information than one figure can carry. That per-city characterisation is now
  # tab_city_characteristics, an SI longtable.
  fig <- (b2 | b1) +
    patchwork::plot_layout(widths = c(0.05, 1), guides = "collect") &
    theme(legend.position = "bottom")

  # Caption numbers, printed so a rebuild on new data shows immediately when the
  # caption has drifted.
  n_clip <- sum(abs(zl$z) > max(Z_LIMITS), na.rm = TRUE)
  message(sprintf(paste0("  [caption] %d standardised outcome variables; %d cities; ",
                         "%d log-transformed variable(s); %d cell(s) blanked as not ",
                         "measured; %d cell(s) clipped at |z|=%.1f"),
                  length(zcols), nrow(arch), sum(PROFILE_FEATURES$log),
                  n_blank, n_clip, max(Z_LIMITS)))

  save_item(fig, "fig1b_outcome_profiles", width = 10, height = 9)
}

if (!exists(".MULTICITY_NORUN")) build_fig1b()
