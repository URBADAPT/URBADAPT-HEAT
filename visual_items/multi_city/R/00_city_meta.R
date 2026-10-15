# =============================================================================
# 00_city_meta.R  --  build cached city metadata: climate metrics + clusters
# Produces visual_items/multi_city/tables/city_metadata.csv with, per city:
#   country/iso3/pop_k/capital  (from configs/<city>.yml)
#   warmseason_mean_t2m, t2m_p95, hot_days  (from the OBSERVED ERA5-Land daily
#                   distribution; MULTICITY_HEAT_SOURCE=model reverts to the
#                   model hazard, which is what the published figures used)
#   climate_cluster (k=3 on [intensity, frequency], labelled by centroid heat)
#   is_exemplar     (cluster medoid = city nearest its cluster centroid)
#   coef_lst_coverage_pct  (greening-cooling coefficient coverage; see below)
#   lcz_*           (LCZ composition: compact/open/other built shares of built
#                    land, built share of the FUA, effective number of classes)
#
# The expensive reads -- the hazard source and the 40 LCZ rasters -- are cached
# in .city_heat_cache.csv and .city_lcz_cache.csv; clustering is always
# recomputed (it depends on the full set of cities).
#
# NOTE: run with Rscript under *PowerShell* -- terra/ncdf4 segfault under the
# Git Bash Rscript on this machine.  From visual_items/multi_city/R:
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
    urau_code = y$urau_code %||% NA_character_,
    stringsAsFactors = FALSE)
}

urau_of <- function(city) {
  cfg <- read_config(city)
  if (is.null(cfg) || is.na(cfg$urau_code)) NA_character_ else cfg$urau_code
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
  # Prefer the run-tagged central file; fall back to the legacy plain name.
  cand <- c(
    city_files(city, "T2M_daily_mean_2020_FUA_degC__.*central\\.nc$", where = "hazard"),
    city_files(city, "^T2M_daily_mean_2020_FUA_degC\\.nc$", where = "hazard"))
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

# ---- heat metrics from the OBSERVED ERA5-Land distribution -------------------
# The preferred source, and the default. The two sources above read the model's
# own 2020 baseline, which is a day-of-year climatological mean: a variance-free
# series. Counting threshold crossings on it is the same Jensen fault that
# suppressed the mortality burden, and it bites hard here. Against the observed
# UrbClim years, the climatology gives Vienna 0 hot days where the record
# averages ~15 (range 1-32), Berlin 0 against ~5, Budapest 5 against ~25.
#
# It also made the climate class a function of the hazard calibration: applying
# the ERA5-Land reference correction moved 8 of 40 cities between classes and
# halved the Hot group, taking Rome, Milan, Barcelona and Naples out of it.
# Climate class is an external annotation in this design, so it must not move
# when the model's hazard is recalibrated.
#
# Masselot's `tmean_distribution.csv` carries the observed ERA5-Land daily-mean
# distribution as 119 percentiles for each of the 854 URAU cities - all 40 of
# ours match. Both metrics are read off it with their original definitions:
# the warm season is the warmest 153/365 of days (May-Sep by length) and the hot
# days are 365 x (1 - F(25 C)). Being observational and fixed, it is immune to
# any future change in the model hazard.
TMEAN_DIST <- file.path(REPO_ROOT, "urban-heat", "10288665", "tmean_distribution.csv")
WARM_FRAC  <- 153 / 365
.tmean_dist_cache <- NULL

tmean_dist <- function() {
  if (is.null(.tmean_dist_cache)) {
    if (!file.exists(TMEAN_DIST)) return(NULL)
    .tmean_dist_cache <<- suppressWarnings(
      readr::read_csv(TMEAN_DIST, show_col_types = FALSE, progress = FALSE))
  }
  .tmean_dist_cache
}

heat_metrics_era5 <- function(city) {
  d <- tmean_dist()
  if (is.null(d)) return(NULL)
  u <- urau_of(city)
  if (is.na(u)) { message(sprintf("  [skip] %s: no urau_code in config", city)); return(NULL) }
  row <- d[d$URAU_CODE == u, , drop = FALSE]
  if (!nrow(row)) { message(sprintf("  [skip] %s: URAU %s not in tmean_distribution", city, u)); return(NULL) }

  pc   <- grep("%$", names(d), value = TRUE)
  prob <- as.numeric(sub("%$", "", pc)) / 100
  val  <- as.numeric(row[1, pc])
  o    <- order(prob); prob <- prob[o]; val <- val[o]
  keep <- !is.na(val); prob <- prob[keep]; val <- val[keep]
  if (length(val) < 3) return(NULL)

  warm <- prob >= (1 - WARM_FRAC)
  # Probability-weighted, because the percentile columns are not equally spaced.
  warm_mean <- if (sum(warm) > 1)
    .trapz(prob[warm], val[warm]) / (max(prob[warm]) - min(prob[warm]))
  else val[warm][1]
  p_hot <- stats::approx(val, prob, xout = T_HOT, rule = 2, ties = "ordered")$y

  data.frame(
    city                = city,
    warmseason_mean_t2m = round(warm_mean, 3),
    t2m_p95             = round(stats::approx(prob, val, xout = 0.95, rule = 2)$y, 3),
    hot_days            = round(365 * (1 - p_hot), 1),
    heat_source         = "era5_land",
    stringsAsFactors = FALSE)
}

.trapz <- function(x, y) sum(diff(x) * (head(y, -1) + tail(y, -1)) / 2)

# Source order. HEAT_SOURCE=model falls back to the model hazard, which is what
# the published figures used; keep it only for reproducing them.
HEAT_SOURCE <- Sys.getenv("MULTICITY_HEAT_SOURCE", "era5")

heat_metrics <- function(city) {
  if (!identical(HEAT_SOURCE, "model")) {
    m <- heat_metrics_era5(city)
    if (!is.null(m)) return(m)
    message(sprintf("  [note] %s: falling back to the model hazard", city))
  }
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
  # And discard a cache built from a different source, so the whole set stays on
  # one basis. Without this the switch to ERA5-Land is a silent no-op: every city
  # is already in the cache under `events_csv`, so nothing is recomputed and the
  # clusters keep their model-hazard values under the new label.
  want <- if (identical(HEAT_SOURCE, "model")) c("events_csv", "netcdf") else "era5_land"
  if (!is.null(cache) && !all(cache$heat_source %in% want)) {
    message(sprintf("  [note] discarding heat cache built from '%s'; want '%s'.",
                    paste(unique(cache$heat_source), collapse = "/"),
                    paste(want, collapse = "/")))
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


# ---- LCZ composition --------------------------------------------------------
# Urban morphology descriptors from each city's `lcz_masked_fua.tif` (the same
# Local Climate Zone raster the tree pathway is applied on). Present for all 40
# cities. Used only as an EXTERNAL descriptor of the outcome-based policy
# archetypes -- never as a clustering variable -- to interpret why cities with
# similar heat exposure get different returns from spatial cooling.
#
# WUDAPT / Stewart-Oke classes: 1-10 are built types, 11-17 (A-G) natural land
# cover. The greening policy is restricted to built classes 1-10, so the
# built/natural split is also the split between the part of the FUA the tree
# policy can act on and the part it cannot.
LCZ_COMPACT <- 1:3        # compact high-, mid-, low-rise
LCZ_OPEN    <- 4:6        # open high-, mid-, low-rise
LCZ_OTHER   <- 7:10       # lightweight, large low-rise, sparsely built, industry
LCZ_BUILT   <- 1:10
LCZ_CACHE   <- file.path(TAB_DIR, ".city_lcz_cache.csv")

.lcz_na_row <- function(city) data.frame(
  city = city, lcz_compact_pct = NA_real_, lcz_open_pct = NA_real_,
  lcz_other_built_pct = NA_real_, lcz_built_pct = NA_real_,
  lcz_diversity = NA_real_, lcz_cells = NA_real_, stringsAsFactors = FALSE)

# compact/open/other are shares OF BUILT LAND (they sum to 100), because the
# question they answer is which built form dominates the fabric; built_pct is
# the share of the whole valid FUA, i.e. the built/natural balance.
lcz_composition <- function(city) {
  p <- file.path(OUTPUTS_BASE, city, "lcz_masked_fua.tif")
  if (!file.exists(p)) {
    message(sprintf("  [skip] %s: no lcz_masked_fua.tif", city))
    return(.lcz_na_row(city))
  }
  if (!requireNamespace("terra", quietly = TRUE)) {
    message("  [skip] LCZ composition: package `terra` not installed.")
    return(.lcz_na_row(city))
  }
  f <- tryCatch(terra::freq(terra::rast(p)), error = function(e) {
    message(sprintf("  [error] %s LCZ raster: %s", city, conditionMessage(e))); NULL })
  if (is.null(f) || !all(c("value", "count") %in% names(f))) return(.lcz_na_row(city))
  f$value <- round(as.numeric(f$value))
  # 0 is the nodata fill outside the FUA mask, not an LCZ class.
  f <- f[!is.na(f$value) & f$value > 0 & is.finite(f$count), , drop = FALSE]
  tot <- sum(f$count)
  if (!nrow(f) || tot <= 0) return(.lcz_na_row(city))
  n_of <- function(cls) sum(f$count[f$value %in% cls])
  built <- n_of(LCZ_BUILT)
  pc <- function(num, den) if (den > 0) round(100 * num / den, 1) else NA_real_
  # Effective number of LCZ classes (exp of Shannon entropy): 1 = monoculture,
  # higher = a more mixed fabric. Preferred over bare entropy because it reads
  # on the same scale as "how many classes does this city really have".
  p_i <- f$count / tot
  data.frame(
    city                = city,
    lcz_compact_pct     = pc(n_of(LCZ_COMPACT), built),
    lcz_open_pct        = pc(n_of(LCZ_OPEN),    built),
    lcz_other_built_pct = pc(n_of(LCZ_OTHER),   built),
    lcz_built_pct       = pc(built, tot),
    lcz_diversity       = round(exp(-sum(p_i * log(p_i))), 2),
    lcz_cells           = tot,
    stringsAsFactors = FALSE)
}

# Same cache-and-top-up pattern as the heat metrics: reading 40 rasters is the
# slow part of the metadata build, and it never changes for a completed city.
load_or_compute_lcz <- function(cities, force = FALSE) {
  cache <- if (!force && file.exists(LCZ_CACHE))
    suppressWarnings(readr::read_csv(LCZ_CACHE, show_col_types = FALSE)) else NULL
  have <- if (!is.null(cache)) cache$city else character(0)
  todo <- setdiff(cities, have)
  if (length(todo)) {
    message(sprintf("Computing LCZ composition for: %s", paste(todo, collapse = ", ")))
    new <- dplyr::bind_rows(lapply(todo, lcz_composition))
    cache <- dplyr::bind_rows(cache, new)
    readr::write_csv(cache, LCZ_CACHE)
  }
  if (is.null(cache)) return(dplyr::bind_rows(lapply(cities, .lcz_na_row)))
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

build_city_meta <- function(force_heat = FALSE, force_lcz = FALSE) {
  banner("City metadata: climate metrics + clusters")
  cities <- discover_cities(require_tables = FALSE)
  cities <- cities[vapply(cities, has_hazard_source, logical(1))]
  if (!length(cities)) { message("No cities with a 2020 hazard source."); return(invisible(NULL)) }

  cfg  <- dplyr::bind_rows(lapply(cities, read_config))
  heat <- load_or_compute_heat(cities, force = force_heat)
  meta <- merge(cfg, heat, by = "city", all = TRUE)

  # Population: prefer the population of the modelled area, which deaths and costs
  # are summed over, to the approximate hand-entered config value (see
  # city_pop_k_model() in _helpers.R). Both are kept in the metadata so the choice
  # stays auditable.
  meta$pop_k_config <- meta$pop_k
  meta$pop_k_model  <- vapply(meta$city, city_pop_k_model, numeric(1))
  meta$pop_k        <- ifelse(is.na(meta$pop_k_model), meta$pop_k_config,
                              meta$pop_k_model)
  meta$pop_source   <- ifelse(is.na(meta$pop_k_model),
                              ifelse(is.na(meta$pop_k_config), "none", "config"),
                              "model")

  meta$coef_lst_coverage_pct <- vapply(meta$city, coef_lst_coverage, numeric(1))

  # Morphology descriptors (external annotations for the policy archetypes).
  meta <- merge(meta, load_or_compute_lcz(cities, force = force_lcz),
                by = "city", all.x = TRUE)

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
                 "is_exemplar", "coef_lst_coverage_pct",
                 "lcz_compact_pct", "lcz_built_pct", "lcz_diversity")],
        row.names = FALSE)
  invisible(meta)
}

if (!exists(".MULTICITY_NORUN")) build_city_meta()
