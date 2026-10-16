# =============================================================================
# fig1_map_band.R  --  top band of Fig 1: where the cities are, and that the model
# resolves risk within them.
#
#   (a) The 40 cities on a map of Europe, centred, coloured by their baseline (2020)
#       heat-attributable death rate and sized by the population of the modelled
#       area, flanked by four zoom-ins (two per side) of the same rate on the 100 m
#       grid, cropped to the district boundaries (the administrative city) and drawn
#       with them. Cities and cells share one log colour scale: deaths per 100,000
#       residents per year under current air conditioning.
#
# The gridded rate is written by urban-heat/validation/gridded_heat_mortality.py,
# which keeps NB05's cell dimension and reproduces its city total; the GeoTIFF is
# read from the city's root folder in the outputs tree:
#   <city>/heat_mortality_grid_<city>_2020.tif   band 3 = deaths per 100k per year
# City rates: interim/marginal_heat_deaths_perC_currentAC_<city>.csv (deaths_T, the
# same NB05 total) over the modelled-area population (city_pop_k_model). Other
# inputs: <city>/<city>_fua.gpkg (location), interim district geometry.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

ZOOM_CITIES <- c("paris", "athens", "berlin", "budapest")   # left column: Paris over Athens; right: Berlin over Budapest
MAP_CRS <- 3035
GRID_YEAR <- 2020
RATE_NAME <- "Heat deaths per 100,000 per year"

# The zoom-ins show the city proper. The district layers of Paris and Berlin also hold
# neighbouring units the OSM query picked up (Saint-Denis; 28 Brandenburg Ortsteile).
ZOOM_KEEP <- list(
  paris  = function(d) grepl("Arrondissement", d$muni_name),
  berlin = function(d) d$muni_name %in% c(
    "Charlottenburg-Wilmersdorf", "Friedrichshain-Kreuzberg", "Lichtenberg",
    "Marzahn-Hellersdorf", "Mitte", "Neukölln", "Pankow", "Reinickendorf", "Spandau",
    "Steglitz-Zehlendorf", "Tempelhof-Schöneberg", "Treptow-Köpenick"))

city_point <- function(city) {
  p <- city_path(city, sprintf("%s_fua.gpkg", city), where = "root")
  if (!file.exists(p)) return(NULL)
  g <- tryCatch(sf::st_read(p, quiet = TRUE), error = function(e) NULL)
  if (is.null(g)) return(NULL)
  g <- sf::st_transform(sf::st_make_valid(g), MAP_CRS)
  xy <- sf::st_coordinates(suppressWarnings(sf::st_centroid(sf::st_union(g))))
  data.frame(city = city, x = xy[1, 1], y = xy[1, 2])
}

# Baseline heat deaths per 100k residents of the modelled area, the city-level
# counterpart of the gridded rate (same NB05 total, same year).
city_rate <- function(city, year = GRID_YEAR) {
  m <- read_city_csv(city, sprintf("marginal_heat_deaths_perC_currentAC_%s.csv", city),
                     where = "interim", quiet = TRUE)
  pop_k <- city_pop_k_model(city, year)
  if (is.null(m) || !is.finite(pop_k)) return(NA_real_)
  d <- m$deaths_T[m$year == year]
  if (!length(d)) NA_real_ else d[1] / (pop_k * 1e3) * 1e5
}

# District outlines (any polygon layer in interim/ other than the AC zones).
district_outline <- function(city) {
  g <- city_files(city, "\\.gpkg$", where = "interim")
  g <- g[!grepl("zones|communities", basename(g))]
  if (!length(g)) return(NULL)
  x <- tryCatch(sf::st_read(g[1], quiet = TRUE), error = function(e) NULL)
  if (is.null(x)) return(NULL)
  if (!is.null(ZOOM_KEEP[[city]])) x <- x[ZOOM_KEEP[[city]](x), ]
  sf::st_transform(sf::st_make_valid(x), MAP_CRS)
}

# Cells inside the district boundaries only, when the city has them.
rate_grid <- function(city, dist, year = GRID_YEAR) {
  p <- city_path(city, sprintf("heat_mortality_grid_%s_%d.tif", city, year), where = "root")
  if (!file.exists(p)) return(NULL)
  r <- terra::rast(p)[[3]]
  if (!is.null(dist)) {
    v <- terra::vect(sf::st_union(dist))
    r <- terra::mask(terra::crop(r, v), v)
  }
  r <- terra::trim(r)                          # drop the empty margin around the city
  d <- as.data.frame(r, xy = TRUE, na.rm = TRUE)
  names(d)[3] <- "rate"
  d[d$rate > 0, , drop = FALSE]
}

# One fill scale for cities and cells, so patchwork collects a single legend.
rate_scale <- function(lim) {
  scale_fill_viridis_c(option = "magma", direction = -1, trans = "log10", limits = lim,
                       oob = scales::squish, name = RATE_NAME,
                       labels = scales::label_number(accuracy = 1),
                       guide = guide_colourbar(direction = "horizontal", title.position = "top",
                                               barwidth = unit(5.5, "cm"), barheight = unit(0.3, "cm")))
}

europe_panel <- function(pts, lim) {
  land <- rnaturalearth::ne_countries(scale = "medium", continent = "Europe", returnclass = "sf")
  land <- sf::st_transform(sf::st_make_valid(land), MAP_CRS)
  pad <- 2.5e5
  ggplot() +
    geom_sf(data = land, fill = "grey92", color = "grey70", linewidth = 0.2) +
    geom_point(data = pts, aes(x, y, size = pop_k, fill = rate), shape = 21,
               color = "white", stroke = 0.5) +
    ggrepel::geom_text_repel(data = pts[pts$zoom, ], aes(x, y, label = city_label), size = 3,
                             fontface = "bold", seed = 1, min.segment.length = 0,
                             box.padding = 0.5, segment.size = 0.3) +
    rate_scale(lim) +
    scale_size_area(max_size = 6, name = "Population (thousands)",
                    breaks = c(500, 2000, 6000), labels = scales::comma,
                    # The keys sit on white: outline them, as the white rings would vanish.
                    guide = guide_legend(direction = "horizontal", title.position = "top",
                                         override.aes = list(colour = "grey35", fill = "grey85"))) +
    coord_sf(crs = MAP_CRS, xlim = range(pts$x) + c(-pad, pad), ylim = range(pts$y) + c(-pad, pad),
             expand = FALSE, datum = NA) +
    labs(x = NULL, y = NULL) +
    # Same base size as the zoom-ins: patchwork merges the two colour bars only if
    # they are identical, text size included.
    theme_void(base_size = 10) +
    theme(
          plot.background = element_rect(fill = "white", color = NA),
          panel.background = element_rect(fill = "white", color = NA))
}

zoom_panel <- function(city, g, dist, lim, tag = NULL) {
  if (is.null(g)) {
    message(sprintf("  [warn] zoom %s left blank: no gridded heat-mortality file", city))
    return(ggplot() + labs(tag = tag, title = city_label(city)) + theme_void(base_size = 10) +
             theme(plot.tag = element_text(face = "bold", size = rel(1.3)),
                   plot.title = element_text(size = rel(0.95), face = "bold", hjust = 0.5)))
  }
  p <- ggplot(g, aes(x, y, fill = rate)) + geom_raster() + rate_scale(lim)
  bb <- c(range(g$x), range(g$y))
  if (!is.null(dist)) {
    p <- p + geom_sf(data = dist, inherit.aes = FALSE, fill = NA, color = "white", linewidth = 0.2)
    bb <- sf::st_bbox(dist)[c("xmin", "xmax", "ymin", "ymax")]
  }
  p + coord_sf(crs = MAP_CRS, xlim = bb[1:2], ylim = bb[3:4], expand = FALSE, datum = NA) +
    labs(tag = tag, title = city_label(city), x = NULL, y = NULL) +
    theme_void(base_size = 10) +
    theme(plot.tag = element_text(face = "bold", size = rel(1.3)),
          plot.title = element_text(size = rel(0.95), face = "bold", hjust = 0.5),
          plot.background = element_rect(fill = "white", color = NA))
}

# Zoom-ins two per side of the centred map; one collected colour legend.
fig1_map_band <- function(cities = discover_cities()) {
  meta <- load_city_meta()
  pts <- do.call(rbind, Filter(Negate(is.null), lapply(cities, city_point)))
  pts$rate <- vapply(pts$city, city_rate, numeric(1))
  pts <- merge(pts, meta[, c("city", "pop_k")], by = "city")
  pts$city_label <- city_label(pts$city)
  pts$zoom <- pts$city %in% ZOOM_CITIES

  dists <- setNames(lapply(ZOOM_CITIES, district_outline), ZOOM_CITIES)
  grids <- setNames(lapply(ZOOM_CITIES, function(c) rate_grid(c, dists[[c]])), ZOOM_CITIES)
  # Scale limits: the city rates and each zoom's 2nd-98th percentile cell rate, per city,
  # so a small hot core (Athens) is not saturated by the larger cities' cell counts.
  cell_q <- unlist(lapply(Filter(Negate(is.null), grids),
                          function(g) stats::quantile(g$rate, c(0.02, 0.98), names = FALSE)))
  lim <- range(c(pts$rate, cell_q), na.rm = TRUE)
  if (!length(cell_q)) message("  [warn] no gridded heat-mortality files; zoom-ins left blank")
  message(sprintf("[caption] city rates: %.1f-%.1f per 100k (median %.1f); shared scale %.1f-%.1f",
                  min(pts$rate, na.rm = TRUE), max(pts$rate, na.rm = TRUE),
                  stats::median(pts$rate, na.rm = TRUE), lim[1], lim[2]))
  for (c in names(Filter(Negate(is.null), grids))) {
    r <- grids[[c]]$rate
    message(sprintf("[caption] zoom %s: cell rate P5 %.1f, median %.1f, P95 %.1f (city %.1f)",
                    city_label(c), stats::quantile(r, 0.05), stats::median(r),
                    stats::quantile(r, 0.95), pts$rate[pts$city == c]))
  }

  # The panel tag sits on the top-left zoom-in, the figure's top-left corner.
  z <- lapply(seq_along(ZOOM_CITIES), function(i)
    zoom_panel(ZOOM_CITIES[i], grids[[ZOOM_CITIES[i]]], dists[[ZOOM_CITIES[i]]], lim,
               tag = if (i == 1) "a" else NULL))
  map <- europe_panel(pts, lim)
  patchwork::wrap_plots(
    patchwork::wrap_plots(z[[1]], z[[2]], ncol = 1),
    map,
    patchwork::wrap_plots(z[[3]], z[[4]], ncol = 1),
    widths = c(0.9, 2, 0.9)) +
    patchwork::plot_layout(guides = "collect") &
    theme(legend.position = "bottom", legend.box = "horizontal",
          legend.title = element_text(size = rel(0.85)), legend.text = element_text(size = rel(0.8)))
}

if (!exists(".MULTICITY_NORUN")) {
  band <- fig1_map_band()
  save_item(band, "fig1_map_band_preview", width = 11.5, height = 6)
}
