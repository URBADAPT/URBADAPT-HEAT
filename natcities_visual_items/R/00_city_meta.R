# =============================================================================
# 00_city_meta.R  --  build cached city metadata: climate metrics + clusters
# Produces natcities_visual_items/tables/city_metadata.csv with, per city:
#   country/iso3/pop_k/capital  (from configs/<city>.yml)
#   warmseason_mean_t2m, t2m_p95, hot_days  (from 2020 baseline hazard netCDF)
#   climate_cluster (k=3 on [intensity, frequency], labelled by centroid heat)
#   is_exemplar     (cluster medoid = city nearest its cluster centroid)
#
# The expensive netCDF read is cached in .city_heat_cache.csv; clustering is
# always recomputed (it depends on the full set of cities).
#
# NOTE: run with Rscript under *PowerShell* -- terra/ncdf4 segfault under the
# Git Bash Rscript on this machine.  From natcities_visual_items/R:
#   Rscript 00_city_meta.R
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}
suppressPackageStartupMessages(library(ncdf4))
`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

CONFIG_DIR <- file.path(REPO_ROOT, "urban-heat", "configs")
HEAT_CACHE <- file.path(TAB_DIR, ".city_heat_cache.csv")
META_OUT   <- file.path(TAB_DIR, "city_metadata.csv")

WARM_MONTHS <- 5:9      # May-Sep, matches the EWS warning season
T_HOT       <- 25       # deg C daily-mean threshold defining a "hot day"
N_CLUSTERS  <- 3

# ---- config parsing ---------------------------------------------------------
read_config <- function(city) {
  p <- file.path(CONFIG_DIR, paste0(city, ".yml"))
  if (!file.exists(p)) return(NULL)
  y <- tryCatch(yaml::read_yaml(p), error = function(e) NULL)
  if (is.null(y)) return(NULL)
  data.frame(
    city      = city,
    city_name = y$city_name %||% city_label(city),
    iso3      = y$wp_iso3 %||% NA_character_,
    country   = y$wp_country %||% NA_character_,
    pop_k     = suppressWarnings(as.numeric(y$pop_k %||% NA)),
    capital   = isTRUE(y$capital),
    stringsAsFactors = FALSE)
}

# ---- heat metrics for the 2020 baseline hazard -------------------------------
# Two equivalent sources, in preference order:
#   1. interim/hazard_events_T2M_daily_<city>.csv -- one row per day with the
#      city-mask spatial mean already reduced (`mean_intensity_citymask_degC`).
#      This is what the cluster runs ship, and it reproduces the netCDF-derived
#      metrics to within 0.03 degC (verified on milan & rome).
#   2. hazard/T2M_daily_mean_2020_FUA_degC*.nc -- the raw grid, kept as a
#      fallback for older local runs that predate the events CSV.
# Note the CSV spans 2020-2050, so it must be filtered to the 2020 baseline.

find_hazard_events_csv <- function(city) {
  p <- city_path(city, sprintf("hazard_events_T2M_daily_%s.csv", city),
                 where = "interim")
  if (file.exists(p)) p else NA_character_
}

find_hazard_nc <- function(city) {
  hd <- file.path(OUTPUTS_BASE, city, "hazard")
  if (!dir.exists(hd)) return(NA_character_)
  # Prefer the run-tagged central file; fall back to the legacy plain name.
  cand <- c(
    list.files(hd, pattern = "T2M_daily_mean_2020_FUA_degC__.*central\\.nc$",
               full.names = TRUE),
    list.files(hd, pattern = "^T2M_daily_mean_2020_FUA_degC\\.nc$",
               full.names = TRUE))
  if (length(cand)) cand[1] else NA_character_
}

# TRUE if either source is present, so discovery does not need to know which.
has_hazard_source <- function(city)
  !is.na(find_hazard_events_csv(city)) || !is.na(find_hazard_nc(city))

# Reduce a vector of daily city-mean temperatures + their months to the metrics.
.heat_from_daily <- function(city, day_mean, mon, source) {
  warm <- mon %in% WARM_MONTHS
  data.frame(
    city                = city,
    warmseason_mean_t2m = round(mean(day_mean[warm], na.rm = TRUE), 3),
    t2m_p95             = round(as.numeric(quantile(day_mean, 0.95, na.rm = TRUE)), 3),
    hot_days            = sum(day_mean >= T_HOT, na.rm = TRUE),
    heat_source         = source,
    stringsAsFactors = FALSE)
}

heat_metrics_csv <- function(city) {
  f <- find_hazard_events_csv(city)
  if (is.na(f)) return(NULL)
  d <- suppressWarnings(readr::read_csv(f, show_col_types = FALSE, progress = FALSE))
  col <- "mean_intensity_citymask_degC"
  if (!all(c("date", col) %in% names(d))) {
    message(sprintf("  [skip] %s: hazard events CSV lacks %s", city, col))
    return(NULL)
  }
  d$date <- as.Date(d$date)
  d <- d[!is.na(d$date) & format(d$date, "%Y") == "2020" & !is.na(d[[col]]), ]
  if (!nrow(d)) { message(sprintf("  [skip] %s: no 2020 rows in hazard events CSV", city)); return(NULL) }
  .heat_from_daily(city, d[[col]], as.integer(format(d$date, "%m")), "events_csv")
}

heat_metrics_nc <- function(city) {
  f <- find_hazard_nc(city)
  if (is.na(f)) return(NULL)
  nc <- nc_open(f)
  on.exit(nc_close(nc))
  mon <- as.integer(ncvar_get(nc, "month"))
  arr <- ncvar_get(nc, "T2M")                 # [time, x, y]
  day_mean <- apply(arr, 1, function(sl) mean(sl, na.rm = TRUE))  # FUA spatial mean
  .heat_from_daily(city, day_mean, mon, "netcdf")
}

heat_metrics <- function(city) {
  m <- heat_metrics_csv(city)
  if (!is.null(m)) return(m)
  m <- heat_metrics_nc(city)
  if (!is.null(m)) return(m)
  message(sprintf("  [skip] %s: no 2020 hazard source", city))
  NULL
}

# ---- greening-cooling coefficient coverage ----------------------------------
# The tree pathway converts greening into cooling through a per-LCZ, per-month
# coefficient (`coef_lst` in interim/<city>_coef_bridge_check.csv). Where that
# coefficient is zero, greening produces no cooling and therefore no avoided
# deaths, no matter how much canopy is added.
#
# Coverage is sparse and very uneven: 0% of summer LCZ-months in Madrid, 5% in
# Barcelona, up to 83% in Bologna (median 35%). It correlates with the tree
# benefit per unit of greening applied (Spearman +0.44) while being unrelated to
# how much greening a city applies (+0.06), to the AC and EWS benefits (+0.10,
# +0.03) and to climate (+0.03) -- i.e. it behaves like a data-coverage artefact
# specific to the greening pathway, not a real city characteristic.
#
# Carried into the metadata so any greening number can be read against it.
coef_lst_coverage <- function(city) {
  d <- read_city_csv(city, sprintf("%s_coef_bridge_check.csv", city),
                     where = "interim", quiet = TRUE)
  if (is.null(d) || !all(c("month", "coef_lst") %in% names(d))) return(NA_real_)
  s <- d$coef_lst[d$month %in% WARM_MONTHS & !is.na(d$coef_lst)]
  if (!length(s)) return(NA_real_)
  round(100 * sum(s != 0) / length(s), 1)
}

load_or_compute_heat <- function(cities, force = FALSE) {
  cache <- if (!force && file.exists(HEAT_CACHE))
    suppressWarnings(readr::read_csv(HEAT_CACHE, show_col_types = FALSE)) else NULL
  # A cache written before the events-CSV path existed has no `heat_source`
  # column; discard it so the whole set is derived from one consistent source.
  if (!is.null(cache) && !"heat_source" %in% names(cache)) {
    message("  [note] discarding pre-existing heat cache (no heat_source column).")
    cache <- NULL
  }
  have <- if (!is.null(cache)) cache$city else character(0)
  todo <- setdiff(cities, have)
  if (length(todo)) {
    message(sprintf("Computing heat metrics for: %s", paste(todo, collapse = ", ")))
    new <- dplyr::bind_rows(lapply(todo, function(c) {
      message(sprintf("  reading %s ...", c)); heat_metrics(c) }))
    cache <- dplyr::bind_rows(cache, new)
    readr::write_csv(cache, HEAT_CACHE)
  }
  cache[cache$city %in% cities, , drop = FALSE]
}

# ---- clustering + labelling -------------------------------------------------
assign_clusters <- function(df, k = N_CLUSTERS) {
  # A city missing either metric cannot be clustered; set it aside and rejoin
  # unlabelled rather than letting kmeans error out on the whole set.
  ok <- stats::complete.cases(df[, c("warmseason_mean_t2m", "hot_days")])
  if (!all(ok)) {
    message(sprintf("  [note] %d city(ies) lack heat metrics; left unclustered: %s",
                    sum(!ok), paste(df$city[!ok], collapse = ", ")))
    lab <- assign_clusters(df[ok, , drop = FALSE], k = k)
    rest <- df[!ok, , drop = FALSE]
    for (cc in setdiff(names(lab), names(rest))) rest[[cc]] <- NA
    return(dplyr::bind_rows(lab, rest[, names(lab), drop = FALSE]))
  }
  X <- scale(df[, c("warmseason_mean_t2m", "hot_days")])
  n <- nrow(df)
  if (n < k) {
    # Too few cities to cluster: provisional fixed-threshold labels on intensity.
    message(sprintf("  [note] only %d city(ies) < k=%d; using provisional ",
                    n, k), "fixed-threshold clusters (finalise once >= 3 cities).")
    df$climate_cluster <- cut(df$warmseason_mean_t2m,
                              breaks = c(-Inf, 18, 22, Inf),
                              labels = c("Cool", "Temperate", "Hot"))
    df$climate_cluster <- as.character(df$climate_cluster)
    df$cluster_provisional <- TRUE
    # medoid = each city is its own exemplar when alone in a cluster
    df$is_exemplar <- ave(df$warmseason_mean_t2m, df$climate_cluster,
                          FUN = function(v) v == max(v)) == 1
    return(df)
  }
  set.seed(42)
  km <- kmeans(X, centers = k, nstart = 25)
  df$.cl <- km$cluster
  # Label clusters by centroid "heat" (mean of the two scaled metrics).
  heat_rank <- rowMeans(km$centers)
  lab_map <- setNames(c("Cool", "Temperate", "Hot")[rank(heat_rank, ties.method = "first")],
                      seq_len(k))
  df$climate_cluster <- lab_map[as.character(df$.cl)]
  df$cluster_provisional <- FALSE
  # Medoid: city nearest its cluster centroid in scaled space.
  df$is_exemplar <- FALSE
  for (cl in unique(df$.cl)) {
    idx <- which(df$.cl == cl)
    cen <- km$centers[cl, ]
    d <- sqrt(rowSums(sweep(X[idx, , drop = FALSE], 2, cen)^2))
    df$is_exemplar[idx[which.min(d)]] <- TRUE
  }
  df$.cl <- NULL
  df
}

build_city_meta <- function(force_heat = FALSE) {
  banner("City metadata: climate metrics + clusters")
  cities <- discover_cities(require_tables = FALSE)
  cities <- cities[vapply(cities, has_hazard_source, logical(1))]
  if (!length(cities)) { message("No cities with a 2020 hazard source."); return(invisible(NULL)) }

  cfg  <- dplyr::bind_rows(lapply(cities, read_config))
  heat <- load_or_compute_heat(cities, force = force_heat)
  meta <- merge(cfg, heat, by = "city", all = TRUE)

  # Population: prefer the model's own exposure population over the approximate
  # hand-entered config value (see city_pop_k_model() in _helpers.R). Both are
  # kept in the metadata so the choice stays auditable.
  meta$pop_k_config <- meta$pop_k
  meta$pop_k_model  <- vapply(meta$city, city_pop_k_model, numeric(1))
  meta$pop_k        <- ifelse(is.na(meta$pop_k_model), meta$pop_k_config,
                              meta$pop_k_model)
  meta$pop_source   <- ifelse(is.na(meta$pop_k_model),
                              ifelse(is.na(meta$pop_k_config), "none", "config"),
                              "model")

  meta$coef_lst_coverage_pct <- vapply(meta$city, coef_lst_coverage, numeric(1))

  meta <- assign_clusters(meta)
  meta$city_label <- city_label(meta$city)
  meta <- meta[order(meta$warmseason_mean_t2m), ]

  readr::write_csv(meta, META_OUT)
  message(sprintf("  [saved] city_metadata.csv (%d cities)", nrow(meta)))
  low <- meta$city_label[!is.na(meta$coef_lst_coverage_pct) &
                           meta$coef_lst_coverage_pct < 25]
  if (length(low)) message(sprintf(
    "  [warn] %d city(ies) below 25%% greening-cooling coefficient coverage; their\n         tree-pathway results are unreliable: %s",
    length(low), paste(low, collapse = ", ")))
  print(meta[, c("city_label", "country", "pop_k", "pop_source",
                 "warmseason_mean_t2m", "hot_days", "climate_cluster",
                 "is_exemplar", "coef_lst_coverage_pct")],
        row.names = FALSE)
  invisible(meta)
}

if (!exists(".NATCITIES_NORUN")) build_city_meta()
