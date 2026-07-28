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

# ---- heat metrics from the 2020 baseline hazard netCDF ----------------------
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

heat_metrics <- function(city) {
  f <- find_hazard_nc(city)
  if (is.na(f)) { message(sprintf("  [skip] %s: no 2020 hazard nc", city)); return(NULL) }
  nc <- nc_open(f)
  on.exit(nc_close(nc))
  mon <- as.integer(ncvar_get(nc, "month"))
  arr <- ncvar_get(nc, "T2M")                 # [time, x, y]
  day_mean <- apply(arr, 1, function(sl) mean(sl, na.rm = TRUE))  # FUA spatial mean
  warm <- mon %in% WARM_MONTHS
  data.frame(
    city               = city,
    warmseason_mean_t2m = round(mean(day_mean[warm], na.rm = TRUE), 3),
    t2m_p95            = round(as.numeric(quantile(day_mean, 0.95, na.rm = TRUE)), 3),
    hot_days           = sum(day_mean >= T_HOT, na.rm = TRUE),
    stringsAsFactors = FALSE)
}

load_or_compute_heat <- function(cities, force = FALSE) {
  cache <- if (!force && file.exists(HEAT_CACHE))
    suppressWarnings(readr::read_csv(HEAT_CACHE, show_col_types = FALSE)) else NULL
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
  cities <- cities[!is.na(vapply(cities, find_hazard_nc, character(1)))]
  if (!length(cities)) { message("No cities with hazard nc."); return(invisible(NULL)) }

  cfg  <- dplyr::bind_rows(lapply(cities, read_config))
  heat <- load_or_compute_heat(cities, force = force_heat)
  meta <- merge(cfg, heat, by = "city", all = TRUE)
  meta <- assign_clusters(meta)
  meta$city_label <- city_label(meta$city)
  meta <- meta[order(meta$warmseason_mean_t2m), ]

  readr::write_csv(meta, META_OUT)
  message(sprintf("  [saved] city_metadata.csv (%d cities)", nrow(meta)))
  print(meta[, c("city_label", "country", "pop_k", "warmseason_mean_t2m",
                 "hot_days", "climate_cluster", "is_exemplar")],
        row.names = FALSE)
  invisible(meta)
}

if (!exists(".NATCITIES_NORUN")) build_city_meta()
