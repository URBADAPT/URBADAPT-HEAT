# =============================================================================
# fig4_city_maps.R  --  Main Fig 4: within-city maps for one city per cluster
#
# Three exemplar cities (the climate-cluster medoids) x three district maps:
#
#   1  Current air-conditioning penetration.
#   2  Coverage gained under the income-targeted (equity) roll-out.
#   3  How the same adaptation budget is redistributed between districts when
#      the equity-targeted mix replaces the uniform one.
#
# ---------------------------------------------------------------------------
# WHAT THIS FIGURE IS NOT. The panels originally wanted were avoided deaths per
# neighbourhood under each policy mix, over a baseline heat death rate. None of
# those exist per district: every mortality output in the run tree is a city
# total by year and age band (baseline_heat_deaths_*, avoided_deaths_*), and they
# cannot be reconstructed. The only gridded hazard shipped is
# t2m_hazard_2020_2050_<city>.npz, shape (4 years, 201, 201) -- an annual summary
# per cell, not the daily series -- and there are no netCDFs anywhere in the
# tree. The exposure-response function is convex and accumulates over days above
# the minimum-mortality temperature, so applying it to an annual mean would
# understate deaths by Jensen's inequality, by a margin that varies city to city.
#
# So panel 2 shows where the policy DEPLOYS cooling, not what it averts. Fixing
# this properly means aggregating impacts to districts upstream in the CBA
# notebooks and shipping one extra CSV.
# ---------------------------------------------------------------------------
#
# Sources, all district-level and all shipped:
#   interim/<districts>_<city>.gpkg      geometry, keyed muni_id (EPSG:4326)
#   <city>_muni_cov_yearly.csv           AC coverage: base / income / uniform
#   <city>_policy_comparison.csv         dGVI under the uniform and equity rules
#   <city>_trees_<district>.csv          pixel count per district
#   <city>_cba_summary.json              city PV totals per pathway
# =============================================================================

.d <- {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  if (length(.f)) dirname(normalizePath(.f)) else getwd()
}
if (!exists("REPO_ROOT")) source(file.path(.d, "_helpers.R"))

# Column identity. A map matrix needs column headers to be readable at all, so
# these are titles on the top row only -- the one place in this figure set where
# a panel carries a title.
MAP_COLS <- c(pen    = "Current AC penetration",
              equity = "Equity-targeted roll-out",
              shift  = "Budget redistribution")

MAP_BORDER <- "black"

# ---- per-city district table -------------------------------------------------

# The district unit is named differently in every country (circoscrizione,
# kerület, stadsdeel, bezirk, ...), so both the geometry and the trees table are
# discovered by pattern rather than named.
district_geom <- function(city) {
  g <- list.files(city_path(city, where = "interim"), pattern = "\\.gpkg$",
                  full.names = TRUE)
  g <- g[!grepl("zones", basename(g))]
  if (!length(g)) return(NULL)
  x <- tryCatch(sf::st_read(g[1], quiet = TRUE), error = function(e) NULL)
  if (is.null(x) || !all(c("muni_id", "muni_name") %in% names(x))) return(NULL)
  x
}

district_trees <- function(city) {
  f <- list.files(city_path(city, where = "tables"),
                  pattern = sprintf("^%s_trees_.*\\.csv$", city), full.names = TRUE)
  if (!length(f)) return(NULL)
  d <- suppressWarnings(readr::read_csv(f[1], show_col_types = FALSE))
  if (!all(c("region_id", "n_pixels") %in% names(d))) return(NULL)
  d[, c("region_id", "n_pixels")]
}

city_district_data <- function(city) {
  geom <- district_geom(city)
  cov  <- read_city_csv(city, sprintf("%s_muni_cov_yearly.csv", city),
                        where = "root", quiet = TRUE)
  pcm  <- read_city_csv(city, sprintf("%s_policy_comparison.csv", city), quiet = TRUE)
  tre  <- district_trees(city)
  cba  <- read_cba(city)
  if (is.null(geom) || is.null(cov) || is.null(pcm) || is.null(cba)) return(NULL)

  need <- c("muni_id", "ac_base_muni", "ac_policy_muni", "ac_policy_uniform",
            "pop_muni", "year")
  if (!all(need %in% names(cov))) return(NULL)
  cov <- cov[cov$year == min(cov$year), need, drop = FALSE]
  # muni_id 0 is the residual bucket for cells outside any named district
  # (Palermo: 141 people). It has no geometry, so it would drop on the join
  # anyway; removing it here keeps it out of the totals used for allocation.
  cov <- cov[cov$muni_id > 0, , drop = FALSE]

  gvi <- c("region_id", "ΔGVI_uniform", "ΔGVI_equity")
  if (!all(gvi %in% names(pcm))) return(NULL)
  pcm <- pcm[, gvi, drop = FALSE]
  names(pcm) <- c("muni_id", "dgvi_uniform", "dgvi_equity")

  d <- merge(sf::st_drop_geometry(geom), cov, by = "muni_id", all.x = TRUE)
  d <- merge(d, pcm, by = "muni_id", all.x = TRUE)
  if (!is.null(tre)) d <- merge(d, tre, by.x = "muni_id", by.y = "region_id", all.x = TRUE)
  else d$n_pixels <- NA_real_

  # --- panel 1: the stock of cooling already in place ---
  d$pen <- 100 * d$ac_base_muni

  # --- panel 2: coverage the income-targeted roll-out adds, percentage points --
  d$equity <- 100 * (d$ac_policy_muni - d$ac_base_muni)

  # --- panel 3: the same city budget, allocated two ways -----------------------
  # Each pathway's city present-value total is distributed over districts by that
  # pathway's own physical driver, so district costs sum exactly to the published
  # city total instead of being re-derived from unit costs and a discount rate.
  # The city total is held fixed across the two rules, so the difference is
  # purely redistributive and sums to zero across districts: it shows who the
  # equity rule spends more on, not a change in the overall bill.
  shr <- function(w) {
    w <- ifelse(is.finite(w) & w > 0, w, 0)
    if (sum(w) > 0) w / sum(w) else rep(0, length(w))
  }
  px <- ifelse(is.finite(d$n_pixels), d$n_pixels, 0)
  cost_of <- function(dcov, dgvi)
    cba$ac_pv    * shr(pmax(dcov, 0) * d$pop_muni) +
    cba$trees_pv * shr(dgvi * px) +
    cba$ews_pv   * shr(d$pop_muni)
  cu <- cost_of(d$ac_policy_uniform - d$ac_base_muni, d$dgvi_uniform)
  ce <- cost_of(d$ac_policy_muni    - d$ac_base_muni, d$dgvi_equity)
  d$cost_uniform <- cu / d$pop_muni
  d$cost_equity  <- ce / d$pop_muni
  # Expressed as a share of the city's whole adaptation budget, not per
  # capita. Per capita, a single district ruins the scale: Amsterdam's
  # Westpoort has 720 residents and the uniform greening rule sends it a
  # large share of the planting budget, so its per-capita shift is
  # -124,337 EUR against a few hundred elsewhere. As a share of the city
  # total the same district is -10%, which is legible and, because the two
  # rules spend the same total, comparable across cities of different size.
  city_total <- sum(cu, na.rm = TRUE)
  d$shift <- if (is.finite(city_total) && city_total > 0)
    100 * (ce - cu) / city_total else NA_real_

  # drop the duplicated name before rejoining geometry, or merge yields
  # muni_name.x / muni_name.y
  d$muni_name <- NULL
  out <- merge(geom[, c("muni_id", "muni_name")], d, by = "muni_id")
  out$city <- city
  out
}

# ---- the build --------------------------------------------------------------

build_fig4_maps <- function() {
  banner("Main Fig 4: within-city maps for one city per climate cluster")
  if (!requireNamespace("sf", quietly = TRUE)) {
    message("Package `sf` is required for the map figure."); return(invisible(NULL)) }
  meta <- load_city_meta()
  if (is.null(meta)) { message("No city metadata."); return(invisible(NULL)) }
  ex <- meta[!is.na(meta$is_exemplar) & meta$is_exemplar, , drop = FALSE]
  ex <- ex[order(match(as.character(ex$climate_cluster), CLUSTER_LEVELS)), ]
  if (!nrow(ex)) { message("No exemplar cities flagged."); return(invisible(NULL)) }
  message(sprintf("  exemplars: %s", paste(sprintf("%s (%s)", ex$city_label,
                                                   ex$climate_cluster), collapse = ", ")))

  dat <- lapply(ex$city, function(c) {
    x <- city_district_data(c)
    if (is.null(x)) message(sprintf("  [skip] %s: district data incomplete", c))
    x
  })
  names(dat) <- ex$city
  dat <- dat[!vapply(dat, is.null, logical(1))]
  if (!length(dat)) { message("No city produced district data."); return(invisible(NULL)) }

  all_d <- do.call(rbind, lapply(dat, function(x)
    sf::st_drop_geometry(x)[, c("city", "pen", "equity", "shift")]))
  message(sprintf("  %d cities, %d districts total", length(dat), nrow(all_d)))

  lim_pen <- range(all_d$pen, na.rm = TRUE)
  lim_eq  <- range(all_d$equity, na.rm = TRUE)
  # symmetric about zero, so the diverging midpoint is the no-change case
  m_shift <- max(abs(all_d$shift), na.rm = TRUE)

  map_theme <- function() theme_void(base_size = 11) +
    theme(legend.position = "bottom",
          legend.key.height = unit(0.28, "cm"),
          legend.key.width  = unit(0.9, "cm"),
          legend.title = element_text(size = rel(0.72)),
          legend.text  = element_text(size = rel(0.62)),
          # the header carries its own bottom margin and the panel a top margin,
          # so a tall city's polygons cannot run into the column label
          plot.title   = element_text(face = "bold", size = rel(0.92), hjust = 0.5,
                                      margin = margin(b = 10)),
          plot.tag     = element_text(face = "bold", size = rel(1.05)),
          plot.margin  = margin(12, 4, 4, 4))

  one_map <- function(x, var, fill_scale, tag, title = NULL, row_label = NULL) {
    g <- ggplot(x) +
      geom_sf(aes(fill = .data[[var]]), colour = MAP_BORDER, linewidth = 0.15) +
      fill_scale +
      coord_sf(datum = NA, expand = TRUE) +
      labs(tag = tag, title = title) +
      map_theme()
    if (!is.null(row_label))
      g <- g + labs(y = row_label) +
        theme(axis.title.y = element_text(angle = 90, face = "bold",
                                          size = rel(0.9), margin = margin(r = 3)))
    g
  }

  tags <- letters
  k <- 0L
  panels <- list()
  for (i in seq_along(dat)) {
    city <- names(dat)[i]
    x <- dat[[city]]
    lbl <- sprintf("%s\n(%s)", city_label(city),
                   as.character(ex$climate_cluster[ex$city == city]))
    specs <- list(
      list("pen", scale_fill_gradient(na.value = "grey88",
             low = "#F7FBFF", high = "#08306B", limits = lim_pen,
             name = "Population with AC (%)",
             labels = scales::label_number(suffix = "%")), MAP_COLS[["pen"]]),
      list("equity", scale_fill_gradient(na.value = "grey88",
             low = "#FCFBFD", high = "#3F007D", limits = lim_eq,
             name = "Coverage gained (pp)"), MAP_COLS[["equity"]]),
      # Diverging, centred on no change: teal where the equity rule spends more
      # per resident than the uniform rule, brown where it spends less.
      list("shift", scale_fill_gradient2(na.value = "grey88",
             low = "#8C510A", mid = "#F5F5F5", high = "#01665E", midpoint = 0,
             limits = c(-m_shift, m_shift),
             name = "Equity minus uniform
(% of city budget)",
             labels = scales::label_number(style_positive = "plus",
                                           suffix = "%")), MAP_COLS[["shift"]]))
    for (j in seq_along(specs)) {
      k <- k + 1L
      s <- specs[[j]]
      panels[[k]] <- one_map(x, s[[1]], s[[2]], tags[k],
                             title = if (i == 1) s[[3]] else NULL,
                             row_label = if (j == 1) lbl else NULL)
    }
  }

  fig <- patchwork::wrap_plots(panels, ncol = length(MAP_COLS), byrow = TRUE) +
    patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom")
  save_item(fig, "fig4_city_maps", width = 11, height = 11)

  message(sprintf(paste0("  [caption] AC penetration %.0f-%.0f%%; equity roll-out ",
                         "adds %.1f-%.1f pp; budget shift %+.1f to %+.1f%% of city total"),
                  lim_pen[1], lim_pen[2], lim_eq[1], lim_eq[2],
                  min(all_d$shift, na.rm = TRUE), max(all_d$shift, na.rm = TRUE)))
  # Districts present in the geometry but absent from the model outputs are drawn
  # grey rather than dropped, so the gap stays visible: Amsterdam annexed Weesp
  # in 2022 and it has no row in muni_cov_yearly.
  for (c in names(dat)) {
    x <- sf::st_drop_geometry(dat[[c]])
    gap <- x$muni_name[!is.finite(x$pen) | !is.finite(x$shift)]
    if (length(gap))
      message(sprintf("  [note] %s: no model data for %s -- drawn grey",
                      city_label(c), paste(gap, collapse = ", ")))
  }
  invisible(dat)
}

if (!exists(".NATCITIES_NORUN")) build_fig4_maps()
