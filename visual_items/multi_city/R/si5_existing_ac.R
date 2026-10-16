# =============================================================================
# si5_existing_ac.R  --  SI: protection already delivered by today's AC stock
# The adaptation pathways are evaluated incrementally against current
# infrastructure. This item shows what the existing AC stock itself delivers in
# 2020, from the no-AC diagnostic branch every run computes in NB05:
#
#   gross   heat deaths with no AC minus heat deaths with current AC
#   waste   the current stock's own waste-heat penalty (outdoor warming from the
#           current penetration, death-weighted cooling activity)
#   net     gross minus waste
#
# all for 2020 over the modelled area. The exposure-response curves are fitted
# to observed mortality, which already reflects historical AC, and AC
# attenuation is applied on top of them; "no AC" is therefore the curves without
# that attenuation, not an observed no-AC world (caption says so).
#
#   (a) Share of no-AC heat deaths avoided vs current AC coverage: gross (open)
#       and net (filled) per city, joined by the waste-heat offset.
#   (b) The same split pooled by climate cluster (sum of deaths).
#   Table tab_existing_ac_by_city: the per-city numbers.
#
# Sources: interim/marginal_heat_deaths_perC_{generic,currentAC}_<city>.csv
# (deaths_T), interim/ac_wasteheat_timeseries_<city>.csv (pen_current,
# penalty_current).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

EXISTING_AC_YEAR <- 2020

existing_ac_data <- function(cities, year = EXISTING_AC_YEAR) {
  at_year <- function(d, col) {
    if (is.null(d) || !all(c("year", col) %in% names(d))) return(NA_real_)
    v <- d[[col]][d$year == year]
    if (!length(v)) NA_real_ else as.numeric(v[1])
  }
  gather_cities(cities, function(c) {
    g  <- read_city_csv(c, sprintf("marginal_heat_deaths_perC_generic_%s.csv", c),
                        where = "interim", quiet = TRUE)
    cu <- read_city_csv(c, sprintf("marginal_heat_deaths_perC_currentAC_%s.csv", c),
                        where = "interim", quiet = TRUE)
    wh <- read_city_csv(c, sprintf("ac_wasteheat_timeseries_%s.csv", c),
                        where = "interim", quiet = TRUE)
    if (is.null(g) || is.null(cu) || is.null(wh)) {
      message(sprintf("  [skip] %s: missing no-AC / current-AC / waste-heat inputs", c))
      return(NULL)
    }
    data.frame(coverage     = at_year(wh, "pen_current"),
               deaths_no_ac = at_year(g, "deaths_T"),
               deaths_cur   = at_year(cu, "deaths_T"),
               waste        = at_year(wh, "penalty_current"),
               pop_k_area   = city_pop_k_model(c, year))
  })
}

build_si5 <- function(cities = discover_cities()) {
  banner("SI 5: protection from the existing AC stock")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  dat <- existing_ac_data(cities)
  if (is.null(dat) || !nrow(dat)) { message("No existing-AC data."); return(invisible(NULL)) }
  dat <- attach_meta(dat, meta)
  dat <- dat[is.finite(dat$deaths_no_ac) & dat$deaths_no_ac > 0, ]
  dat$gross     <- dat$deaths_no_ac - dat$deaths_cur
  dat$net       <- dat$gross - dat$waste
  dat$gross_pct <- 100 * dat$gross / dat$deaths_no_ac
  dat$net_pct   <- 100 * dat$net / dat$deaths_no_ac

  # --- (a) per city ----------------------------------------------------------
  dat$offset_pct <- dat$gross_pct - dat$net_pct
  lab <- rbind(standouts(dat, "offset_pct", n_high = 4, n_low = 0),
               standouts(dat, "net_pct", n_high = 2, n_low = 2))
  lab <- lab[!duplicated(lab$city), ]
  pts <- rbind(
    data.frame(dat[, c("city", "coverage", "climate_cluster")], pct = dat$gross_pct, kind = "Gross"),
    data.frame(dat[, c("city", "coverage", "climate_cluster")], pct = dat$net_pct,
               kind = "Net of its own waste heat"))
  pa <- ggplot(dat, aes(100 * coverage, color = climate_cluster)) +
    geom_segment(aes(xend = 100 * coverage, y = net_pct, yend = gross_pct),
                 linewidth = 0.5, alpha = 0.6, show.legend = FALSE) +
    geom_point(data = pts, aes(y = pct, shape = kind), fill = "white", size = 2.2, stroke = 0.8) +
    scale_shape_manual(values = c("Gross" = 21, "Net of its own waste heat" = 16), name = NULL) +
    ggrepel::geom_text_repel(data = lab, aes(y = gross_pct, label = city_label), size = 2.6,
                             color = "grey25", seed = 1, min.segment.length = 0,
                             box.padding = 0.35, point.padding = 0.2, segment.color = "grey60",
                             segment.size = 0.25, max.overlaps = Inf, show.legend = FALSE) +
    cluster_scale() +
    scale_x_continuous(labels = scales::label_number(suffix = "%")) +
    scale_y_continuous(labels = scales::label_number(suffix = "%"), limits = c(0, NA)) +
    labs(tag = "a", x = sprintf("Current AC coverage, %d", EXISTING_AC_YEAR),
         y = "No-AC heat deaths avoided by the existing stock") +
    theme_multicity()

  # --- (b) pooled by cluster -------------------------------------------------
  pool <- dat |>
    dplyr::filter(!is.na(climate_cluster)) |>
    dplyr::group_by(climate_cluster) |>
    dplyr::summarise(no_ac = sum(deaths_no_ac), net = sum(net), waste = sum(waste),
                     n = dplyr::n(), .groups = "drop") |>
    dplyr::mutate(climate_cluster = as.character(climate_cluster))
  all <- data.frame(climate_cluster = "All cities", no_ac = sum(dat$deaths_no_ac),
                    net = sum(dat$net), waste = sum(dat$waste), n = nrow(dat))
  pool <- rbind(as.data.frame(pool), all)
  pool$climate_cluster <- factor(pool$climate_cluster, levels = rev(c(CLUSTER_LEVELS, "All cities")))
  long <- rbind(
    data.frame(group = pool$climate_cluster, part = "Net protection", pct = 100 * pool$net / pool$no_ac),
    data.frame(group = pool$climate_cluster, part = "Offset by its own waste heat",
               pct = 100 * pool$waste / pool$no_ac))
  long$part <- factor(long$part, levels = c("Offset by its own waste heat", "Net protection"))
  tot <- data.frame(group = pool$climate_cluster, gross = 100 * (pool$net + pool$waste) / pool$no_ac,
                    net = 100 * pool$net / pool$no_ac, n = pool$n)
  pb <- ggplot(long, aes(pct, group, fill = part)) +
    geom_col(width = 0.62) +
    geom_text(data = tot, aes(gross, group, label = sprintf("%.0f%% gross, %.0f%% net (n = %d)", gross, net, n)),
              inherit.aes = FALSE, hjust = -0.05, size = 3, color = "grey25") +
    scale_fill_manual(values = c("Net protection" = "grey30", "Offset by its own waste heat" = "grey75"),
                      name = NULL, guide = guide_legend(reverse = TRUE)) +
    scale_x_continuous(labels = scales::label_number(suffix = "%"),
                       expand = expansion(mult = c(0, 0.75))) +
    labs(tag = "b", x = "Share of no-AC heat deaths, pooled", y = NULL) +
    theme_multicity() +
    theme(panel.grid.major.y = element_blank(), panel.grid.major.x = element_line(color = "grey90"))

  fig <- (pa | pb) + patchwork::plot_layout(widths = c(1.15, 1)) &
    theme(legend.position = "bottom", legend.box = "vertical",
          legend.text = element_text(size = rel(0.85)))
  save_item(fig, "si5_existing_ac", width = 12.5, height = 5.4)

  # --- table -----------------------------------------------------------------
  t <- dat[order(dat$climate_cluster, dat$city_label), ]
  out <- data.frame(
    City = t$city_label,
    Cluster = as.character(t$climate_cluster),
    `AC coverage (%)` = round(100 * t$coverage, 0),
    `Deaths, no AC` = round(t$deaths_no_ac, 0),
    `Deaths, current AC` = round(t$deaths_cur, 0),
    `Avoided, gross` = round(t$gross, 1),
    `Own waste heat` = round(t$waste, 1),
    `Avoided, net` = round(t$net, 1),
    `Net (% of no-AC)` = round(t$net_pct, 1),
    `Net / 100k` = round(per_100k(t$net, t$pop_k_area), 2),
    check.names = FALSE)
  save_table(out, "tab_existing_ac_by_city",
             caption = paste(sprintf("Protection delivered by the existing AC stock in %d.", EXISTING_AC_YEAR),
               "Annual heat-attributable deaths over the modelled area with no AC attenuation and with",
               "current AC coverage; the gross difference; the waste-heat penalty of the current stock",
               "(outdoor warming from current penetration, weighted by daily cooling activity); and the net",
               "protection. The exposure-response curves are fitted to observed mortality, which already",
               "reflects historical AC, so `no AC' denotes the curves without the AC attenuation the model",
               "applies, not an observed no-AC counterfactual. Per 100\\,000 divides by the population of",
               "the modelled area."),
             label = "tab:existing_ac_by_city",
             longtable = TRUE, size = "\\scriptsize",
             align = c("l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "r"),
             digits = c(0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2))

  # Numbers the text quotes, so they are generated rather than transcribed.
  message(sprintf("[caption] %d cities, %d: no-AC %.0f, current AC %.0f deaths; gross %.0f (%.1f%%), own waste heat %.0f, net %.0f (%.1f%%)",
                  nrow(dat), EXISTING_AC_YEAR, sum(dat$deaths_no_ac), sum(dat$deaths_cur),
                  sum(dat$gross), 100 * sum(dat$gross) / sum(dat$deaths_no_ac), sum(dat$waste),
                  sum(dat$net), 100 * sum(dat$net) / sum(dat$deaths_no_ac)))
  message(sprintf("[caption] median city: gross %.1f%%, net %.1f%% of no-AC deaths; waste heat offsets %.0f%% of gross in the pooled hot cluster",
                  median(dat$gross_pct), median(dat$net_pct),
                  with(pool[pool$climate_cluster == "Hot", ], 100 * waste / (net + waste))))
  for (k in seq_len(nrow(pool)))
    message(sprintf("[caption] %s (n=%d): gross %.1f%%, net %.1f%%", pool$climate_cluster[k], pool$n[k],
                    100 * (pool$net[k] + pool$waste[k]) / pool$no_ac[k], 100 * pool$net[k] / pool$no_ac[k]))
  invisible(out)
}

if (!exists(".MULTICITY_NORUN")) build_si5()
