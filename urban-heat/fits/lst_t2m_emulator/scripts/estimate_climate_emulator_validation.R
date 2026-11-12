# estimate_climate_emulator_validation.R
# Extended validation: spline comparison, temporal holdout, city-holdout
#
# This script extends estimate_climate_emulator_LST_T2M_multicities.R with:
#   - Rome spline vs quadratic comparison
#   - Holdout-safe model specifications (factor(month) instead of ym FE)
#   - Within-city temporal holdout (train 2008-2015, test 2016-2017)
#   - City-holdout (leave-one-city-out) validation
#
# METRIC CHOICE (inherited from main script):
#   - LST = daily MAXIMUM land surface temperature
#   - T2M = daily MEAN air temperature at 2m
#

# Source the main emulator script to get helper functions and configuration
# (will NOT run Rome because of sys.nframe() guard)
source("estimate_climate_emulator_LST_T2M_multicities.R", local = FALSE)

# Additional libraries for splines
library(splines)

# CONFIGURATION

VALIDATION_DIR <- "outputs/emulator/validation"
dir.create(VALIDATION_DIR, showWarnings = FALSE, recursive = TRUE)

# Panel cache directory
PANEL_CACHE_DIR <- file.path(VALIDATION_DIR, "panel_cache")
dir.create(PANEL_CACHE_DIR, showWarnings = FALSE, recursive = TRUE)

# City subset for validation
VALIDATION_CITIES <- c("Rome", "Paris", "Berlin", "Madrid", "Stockholm")

# Temporal holdout split
TRAIN_YEARS <- 2008:2015
TEST_YEARS <- 2016:2017

# Memory management: max points per city for validation
# Keep all days for sampled points (better than random row sampling)
VALIDATION_MAX_POINTS <- NULL  # Set to NULL to disable sampling

# Helper to build output filename with sample info
validation_filename <- function(base_name, ext = "csv") {
  if (!is.null(VALIDATION_MAX_POINTS)) {
    sprintf("%s_%dpoints.%s", base_name, VALIDATION_MAX_POINTS, ext)
  } else {
    sprintf("%s_full.%s", base_name, ext)
  }
}

# HOLDOUT-SAFE MODEL SPECIFICATIONS
#
# For temporal holdout, we cannot use ym (year-month) fixed effects because
# they cannot extrapolate to unseen year-month combinations.
#
# HOLDOUT-SAFE ALTERNATIVES:
#   1. factor(month) - captures monthly seasonality, generalises across years
#   2. sin/cos of day-of-year - smooth seasonal control
#   3. Both combined
#
# We use factor(month) as the primary holdout-safe specification because:
#   - Simple and interpretable
#   - Captures the main seasonal pattern
#   - No risk of overfitting to specific year-month combinations
#

# HELPER: POINT SAMPLING FOR MEMORY MANAGEMENT

#' Sample points while keeping all days for selected points
#' This is better than random row sampling because it preserves temporal structure
sample_points_panel <- function(panel_df, n_points_keep = VALIDATION_MAX_POINTS) {
  if (is.null(n_points_keep)) return(panel_df)

  unique_points <- unique(panel_df$point_id)
  if (length(unique_points) <= n_points_keep) return(panel_df)

  set.seed(42)  # reproducible sampling
  keep_ids <- sample(unique_points, n_points_keep)

  sampled <- panel_df %>% filter(point_id %in% keep_ids)
  message("    Sampled ", n_points_keep, " points (", format(nrow(sampled), big.mark=","),
          " obs from ", format(nrow(panel_df), big.mark=","), ")")
  return(sampled)
}

#' Sample points stratified by a static per-point feature (e.g. LCZ).
#'
#' Keeps `frac` of the points within each stratum, preserving the distribution
#' of the stratification variable in the sample. All days are retained for
#' each kept point, so the panel's temporal structure is intact (this matters
#' for fixed effects and clustering at the point level).
#'
#' Designed to be cheap on memory: it doesn't build any intermediate wide
#' data frame — it pulls the two relevant columns as plain vectors, runs
#' `!duplicated` to collapse to one row per point, samples, and applies a
#' single logical filter on the original panel.
#'
#' @param panel_df Long-format panel (data.frame or data.table).
#' @param strat_col Column name to stratify on (must be constant within
#'   point_id — typically `"lcz"`). If absent from the panel, falls back to
#'   non-stratified random point sampling.
#' @param frac Fraction of points to keep within each stratum, in (0, 1].
#'   Values >= 1 or NULL return the panel unchanged.
#' @param min_per_stratum Minimum points to keep per stratum (lower bound on
#'   `ceiling(length(ids) * frac)`); applied per stratum, capped by the
#'   number of points actually available in that stratum.
#' @param seed RNG seed for reproducibility.
stratified_sample_points <- function(panel_df, strat_col = "lcz",
                                      frac = 0.5, min_per_stratum = 5,
                                      seed = 42) {
  if (is.null(frac) || frac >= 1) return(panel_df)
  if (!"point_id" %in% names(panel_df)) {
    warning("stratified_sample_points: no `point_id` column; returning unchanged")
    return(panel_df)
  }

  pid   <- panel_df$point_id
  first <- !duplicated(pid)              # one row per unique point
  upid  <- pid[first]

  if (!is.null(strat_col) && strat_col %in% names(panel_df)) {
    ustrat <- panel_df[[strat_col]][first]
    set.seed(seed)
    keep_ids <- unlist(
      lapply(split(upid, ustrat), function(ids) {
        n_keep <- max(min_per_stratum, ceiling(length(ids) * frac))
        n_keep <- min(n_keep, length(ids))
        if (length(ids) <= n_keep) ids else sample(ids, n_keep)
      }),
      use.names = FALSE
    )
  } else {
    if (!is.null(strat_col)) {
      warning("stratified_sample_points: column '", strat_col,
              "' not in panel; falling back to non-stratified sampling")
    }
    set.seed(seed)
    n_keep <- max(min_per_stratum, ceiling(length(upid) * frac))
    n_keep <- min(n_keep, length(upid))
    keep_ids <- if (n_keep >= length(upid)) upid else sample(upid, n_keep)
  }

  keep_mask <- pid %in% keep_ids
  panel_df  <- panel_df[keep_mask, , drop = FALSE]
  # Force the old object to be collected (the row-subset above creates a copy)
  gc(verbose = FALSE)

  message("    Stratified sample (", if (is.null(strat_col)) "unstratified" else strat_col,
          ", frac=", frac, "): kept ",
          format(length(keep_ids), big.mark = ","), "/",
          format(length(upid), big.mark = ","), " points -> ",
          format(nrow(panel_df), big.mark = ","), " obs")
  panel_df
}

audit_point_sample <- function(city, n_points_keep = 800, years = 2008:2017) {
  
  panel_df <- load_or_build_city_panel(
    city,
    years = years,
    apply_sampling = FALSE
  )
  
  pts_full <- panel_df %>%
    distinct(point_id, lcz, out_b, build_h, build_v, pop_dens, water, elevation)
  
  rm(panel_df)
  gc()
  
  set.seed(42)
  keep_ids <- sample(unique(pts_full$point_id), min(n_points_keep, nrow(pts_full)))
  pts_samp <- pts_full %>% filter(point_id %in% keep_ids)
  
  compare_num <- function(x_full, x_samp, name) {
    data.frame(
      variable = name,
      full_mean = mean(x_full, na.rm = TRUE),
      samp_mean = mean(x_samp, na.rm = TRUE),
      full_p90 = quantile(x_full, 0.90, na.rm = TRUE),
      samp_p90 = quantile(x_samp, 0.90, na.rm = TRUE),
      full_p99 = quantile(x_full, 0.99, na.rm = TRUE),
      samp_p99 = quantile(x_samp, 0.99, na.rm = TRUE)
    )
  }
  
  num_cmp <- bind_rows(
    compare_num(pts_full$out_b,      pts_samp$out_b,      "out_b"),
    compare_num(pts_full$build_h,    pts_samp$build_h,    "build_h"),
    compare_num(pts_full$build_v,    pts_samp$build_v,    "build_v"),
    compare_num(pts_full$pop_dens,   pts_samp$pop_dens,   "pop_dens"),
    compare_num(pts_full$water,      pts_samp$water,      "water"),
    compare_num(pts_full$elevation,  pts_samp$elevation,  "elevation")
  )
  
  lcz_cmp <- full_join(
    pts_full %>%
      count(lcz) %>%
      mutate(full_share = n / sum(n)) %>%
      select(lcz, full_share),
    pts_samp %>%
      count(lcz) %>%
      mutate(samp_share = n / sum(n)) %>%
      select(lcz, samp_share),
    by = "lcz"
  ) %>%
    arrange(lcz)
  
  list(
    numeric = num_cmp,
    lcz = lcz_cmp,
    keep_ids = keep_ids
  )
}

# HELPER: TRAINING-DEFINED SPLINE BASIS
#
# For honest holdout validation, spline knots must be computed from training
# data only, then applied to test data. This prevents information leakage.
#

#' Compute spline knots from training data
#' @param x Training T2M values
#' @param df Degrees of freedom (default 4)
#' @return List with internal knots and boundary knots
compute_spline_knots <- function(x, df = 4) {
  # Internal knots at quantiles
  n_internal <- df - 1
  probs <- seq(0, 1, length.out = n_internal + 2)[-c(1, n_internal + 2)]
  # Use unique() to avoid duplicated knots if quantiles coincide

  knots <- unique(as.numeric(quantile(x, probs = probs, na.rm = TRUE)))
  boundary_knots <- range(x, na.rm = TRUE)

  list(knots = knots, boundary = boundary_knots, df = df)
}

#' Build spline basis using pre-computed knots
#' @param x T2M values (train or test)
#' @param knots_info List from compute_spline_knots()
#' @return Matrix of spline basis columns
build_spline_basis <- function(x, knots_info) {
  splines::ns(x, knots = knots_info$knots, Boundary.knots = knots_info$boundary)
}

#' Add spline columns to data frame using training-defined knots
#' @param df Data frame with t2m column
#' @param knots_info Pre-computed knots from training data
#' @return Data frame with spline_1, spline_2, ... columns added
add_spline_columns <- function(df, knots_info) {
  spline_mat <- build_spline_basis(df$t2m, knots_info)
  spline_df <- as.data.frame(spline_mat)
  names(spline_df) <- paste0("spline_", seq_len(ncol(spline_df)))
  cbind(df, spline_df)
}

# PANEL CACHING FUNCTIONS

#' Build a cache filename for a city panel
panel_cache_file <- function(city_provide, years = 2008:2017) {
  city_slug <- tolower(gsub(" ", "_", city_provide))
  file.path(
    PANEL_CACHE_DIR,
    sprintf(
      "%s_LST%s_T2M%s_%d_%d_panel_full.rds",
      city_slug, LST_STAT, T2M_STAT, min(years), max(years)
    )
  )
}

#' Load cached city panel or build and save it
#' @param apply_sampling If TRUE (default), apply point sampling to reduce memory
#' @param cols_keep Optional character vector of column names. If supplied, the
#'   cached panel is immediately subset to these columns after readRDS, which
#'   substantially reduces peak RAM when the caller doesn't need all derived
#'   variables (e.g. the pooled emulator export only needs raw covariates).
#' @param sample_frac Optional fraction in (0, 1]. If supplied and < 1, the
#'   panel is subsampled at the POINT level (all days retained for each kept
#'   point) right after the column subset. Stratified by `sample_strat_col`
#'   so the distribution of that variable is preserved.
#' @param sample_strat_col Column to stratify the point sample on. Must be
#'   constant within `point_id`. Defaults to `"lcz"`.
#' @param sample_min_per_stratum Minimum points to retain per stratum.
#' @param sample_seed RNG seed for reproducibility of the point sample.
load_or_build_city_panel <- function(city_provide, years = 2008:2017,
                                      force_rebuild = FALSE, apply_sampling = TRUE,
                                      cols_keep = NULL,
                                      sample_frac = NULL,
                                      sample_strat_col = "lcz",
                                      sample_min_per_stratum = 5,
                                      sample_seed = 42) {
  cache_file <- panel_cache_file(city_provide, years)

  if (file.exists(cache_file) && !force_rebuild) {
    message("  Loading cached panel for ", city_provide, "...")
    panel_df <- readRDS(cache_file)
  } else {
    panel_df <- build_city_panel(city_provide, years = years)
    if (!is.null(panel_df)) {
      saveRDS(panel_df, cache_file)
      message("  Saved panel cache: ", cache_file)
    }
  }

  # Drop unused columns ASAP to keep the in-memory footprint small.
  # Cached panels contain many derived columns (x/y, ym, doy, sin/cos, log_*,
  # t2m_c, ...) that downstream callers often recompute. Subsetting here avoids
  # carrying them through bind / pooling steps.
  if (!is.null(panel_df) && !is.null(cols_keep)) {
    keep <- intersect(cols_keep, names(panel_df))
    panel_df <- panel_df[, keep, drop = FALSE]
  }

  # Stratified point subsample (e.g. 50% of points per LCZ). Run this BEFORE
  # the legacy point-cap below so both can stack if desired. All days are
  # kept for each retained point, so panel temporal structure is preserved.
  if (!is.null(panel_df) && !is.null(sample_frac) && sample_frac < 1) {
    panel_df <- stratified_sample_points(
      panel_df,
      strat_col = sample_strat_col,
      frac = sample_frac,
      min_per_stratum = sample_min_per_stratum,
      seed = sample_seed
    )
  }

  # Apply legacy point cap (used by per-city validation steps) for memory mgmt
  if (!is.null(panel_df) && apply_sampling && !is.null(VALIDATION_MAX_POINTS)) {
    panel_df <- sample_points_panel(panel_df, VALIDATION_MAX_POINTS)
  }

  panel_df
}

# HELPER FUNCTIONS FOR VALIDATION

#' Evaluate model on a given dataset (allows train/test split evaluation)
#' @param model Fitted fixest model
#' @param data Data frame to evaluate on
#' @param name Model name
#' @return Data frame with evaluation metrics
eval_model_on_data <- function(model, data, name = "model") {
  pred <- predict(model, newdata = data)
  ok <- is.finite(pred) & is.finite(data$lst)

  if (sum(ok) < 100) {
    return(data.frame(
      model = name, n = sum(ok), rmse = NA, mae = NA,
      r2_pred = NA, rmse_hot = NA, mae_hot = NA
    ))
  }

  y <- data$lst[ok]
  yhat <- pred[ok]
  err <- y - yhat

  rmse <- sqrt(mean(err^2))
  mae <- mean(abs(err))
  r2_pred <- 1 - sum((y - yhat)^2) / sum((y - mean(y))^2)

  # Hot-tail metrics (top 10%)
  q90 <- quantile(y, 0.9, na.rm = TRUE)
  hot <- y >= q90
  if (sum(hot) > 10) {
    rmse_hot <- sqrt(mean((y[hot] - yhat[hot])^2))
    mae_hot <- mean(abs(y[hot] - yhat[hot]))
  } else {
    rmse_hot <- NA
    mae_hot <- NA
  }

  data.frame(
    model = name,
    n = length(y),
    rmse = round(rmse, 3),
    mae = round(mae, 3),
    r2_pred = round(r2_pred, 4),
    rmse_hot = round(rmse_hot, 3),
    mae_hot = round(mae_hot, 3)
  )
}

#' Build panel for a city (reuses main script logic but returns panel only)
#' @param city_provide City name
#' @param years Years to include
#' @return Panel data frame or NULL on failure
build_city_panel <- function(city_provide, years = 2008:2017) {

  city_gvi <- CITY_MAPPING[[city_provide]]
  if (is.null(city_gvi)) city_gvi <- city_provide

  message("\n  Building panel for: ", city_provide)

  # Load GVI
  gvi_all <- readRDS("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/ugs/after_points_100425_citynames.rds")
  gvi_agg <- gvi_all %>%
    filter(city == city_gvi) %>%
    group_by(x, y) %>%
    summarise(out_b = mean(out_b, na.rm = TRUE), .groups = "drop")

  if (nrow(gvi_agg) == 0) {
    warning("No GVI points for ", city_gvi)
    return(NULL)
  }

  gvi_agg$out_b <- gvi_agg$out_b / 100

  # Load climate data
  lst_files <- sapply(years, function(y) {
    download_provide_file(city_provide, y, LST_STAT, "LST")
  })
  t2m_files <- sapply(years, function(y) {
    download_provide_file(city_provide, y, T2M_STAT, "T2M")
  })

  lst_ok <- file.exists(lst_files) & file.size(lst_files) > 10000
  t2m_ok <- file.exists(t2m_files) & file.size(t2m_files) > 10000

  if (!all(lst_ok) || !all(t2m_ok)) {
    warning("Missing data files for ", city_provide)
    return(NULL)
  }

  # Load rasters
  lst_data <- load_nc_stack(lst_files)
  lst_stack <- lst_data$stack
  epsg_city <- lst_data$epsg

  t2m_data <- load_nc_stack(t2m_files)
  t2m_stack <- t2m_data$stack

  # Grid check
  if (!isTRUE(terra::compareGeom(lst_stack[[1]], t2m_stack[[1]], stopOnError = FALSE))) {
    warning("Grid mismatch for ", city_provide)
    return(NULL)
  }

  n_layers <- min(nlyr(lst_stack), nlyr(t2m_stack))
  lst_stack <- lst_stack[[1:n_layers]]
  t2m_stack <- t2m_stack[[1:n_layers]]

  # Covariates
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)

  lcz_rast <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/climate/lcz/lcz_filter_v3.tif")
  gvi_agg$lcz <- terra::extract(lcz_rast, vect(pts_wgs))[[2]]

  build_h <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif")
  build_v <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_V_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  pop_dens <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_POP_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  water <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/climate/provide_urban_climate_data/c_gls_WB300_202306010000_GLOBE_S2_V2.0.1.nc", lyrs = "WB")

  gvi_agg$build_h <- terra::extract(build_h, vect(pts_wgs))[, 2]
  gvi_agg$build_v <- terra::extract(build_v, vect(pts_wgs))[, 2]
  gvi_agg$pop_dens <- terra::extract(pop_dens, vect(pts_wgs))[, 2]
  gvi_agg$water <- terra::extract(water, vect(pts_wgs))[, 2]

  gvi_agg$build_h[is.na(gvi_agg$build_h)] <- 0
  gvi_agg$build_v[is.na(gvi_agg$build_v)] <- 0
  gvi_agg$pop_dens[is.na(gvi_agg$pop_dens)] <- 0
  gvi_agg$water[is.na(gvi_agg$water)] <- 0

  # Elevation
  gvi_agg$elevation <- 0
  tryCatch({
    elev_pts <- get_elev_point(pts_wgs, prj = 4326, src = "aws", z = 10)
    gvi_agg$elevation <- elev_pts$elevation
    gvi_agg$elevation[is.na(gvi_agg$elevation)] <- 0
  }, error = function(e) {
    warning("Elevation failed for ", city_provide)
  })

  # Filter LCZ
  gvi_agg <- gvi_agg %>% filter(lcz >= 1 & lcz <= 10)
  if (nrow(gvi_agg) < 100) {
    warning("Too few points for ", city_gvi)
    return(NULL)
  }

  # Extract
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)
  pts_proj <- st_transform(pts_wgs, epsg_city)

  lst_extract <- terra::extract(lst_stack, vect(pts_proj))
  lst_mat <- as.matrix(lst_extract[, -1])

  t2m_extract <- terra::extract(t2m_stack, vect(pts_proj))
  t2m_mat <- as.matrix(t2m_extract[, -1])

  lst_mat <- to_celsius_safe(lst_mat)
  t2m_mat <- to_celsius_safe(t2m_mat)

  # Build panel
  n_days <- ncol(lst_mat)
  dates <- c()
  for (y in sort(years)) {
    start_date <- as.Date(paste0(y, "-01-01"))
    end_date <- as.Date(paste0(y, "-12-31"))
    dates <- c(dates, seq(start_date, end_date, by = "day"))
  }
  dates <- as.Date(dates, origin = "1970-01-01")
  dates <- dates[1:min(length(dates), n_days)]

  n_points <- nrow(gvi_agg)
  n_days <- length(dates)

  panel_df <- data.frame(
    city = city_provide,
    point_id = rep(1:n_points, n_days),
    date = rep(dates, each = n_points),
    lst = as.vector(lst_mat),
    t2m = as.vector(t2m_mat),
    x = rep(gvi_agg$x, n_days),
    y = rep(gvi_agg$y, n_days),
    out_b = rep(gvi_agg$out_b, n_days),
    build_h = rep(gvi_agg$build_h, n_days),
    build_v = rep(gvi_agg$build_v, n_days),
    pop_dens = rep(gvi_agg$pop_dens, n_days),
    water = rep(gvi_agg$water, n_days),
    elevation = rep(gvi_agg$elevation, n_days),
    lcz = rep(gvi_agg$lcz, n_days)
  )

  # Add unique cluster ID (city + point combination) - character for simplicity
  panel_df$city_point_id <- paste(panel_df$city, panel_df$point_id, sep = "_")

  panel_df$year <- as.integer(format(panel_df$date, "%Y"))
  panel_df$month <- as.integer(format(panel_df$date, "%m"))
  panel_df$ym <- format(panel_df$date, "%Y-%m")
  panel_df$doy <- as.integer(format(panel_df$date, "%j"))

  panel_df <- panel_df %>%
    filter(!is.na(lst) & !is.infinite(lst) &
           !is.na(t2m) & !is.infinite(t2m))

  panel_df$lcz <- factor(panel_df$lcz, levels = 1:10)

  # Derived variables
  panel_df <- panel_df %>%
    mutate(
      log_pop_dens = log1p(pop_dens),
      log_build_h = log1p(build_h),
      log_build_v = log1p(build_v),
      t2m_c = t2m - mean(t2m, na.rm = TRUE),
      # Seasonality terms for holdout-safe models
      sin_doy = sin(2 * pi * doy / 365),
      cos_doy = cos(2 * pi * doy / 365)
    )

  message("    Panel: ", format(nrow(panel_df), big.mark=","), " obs, ",
          n_distinct(panel_df$point_id), " points")

  return(panel_df)
}

# STEP 0: ROME SPLINE VS QUADRATIC COMPARISON

run_rome_spline_comparison <- function() {

  cat("\n")
  cat("================================================================================\n")
  cat("  STEP 0: ROME SPLINE VS QUADRATIC COMPARISON\n")
  cat("  LST = daily ", toupper(LST_STAT), " | T2M = daily ", toupper(T2M_STAT), "\n")
  cat("================================================================================\n\n")

  # Load or build Rome panel (NO SAMPLING for clean functional form comparison)
  cat("NOTE: Using FULL Rome panel (no sampling) for functional form comparison.\n\n")
  panel_df <- load_or_build_city_panel("Rome", years = 2008:2017, apply_sampling = FALSE)

  if (is.null(panel_df)) {
    stop("Failed to load/build Rome panel")
  }

  # Ensure derived variables exist
  if (!"t2m_c" %in% names(panel_df)) {
    panel_df <- panel_df %>%
      mutate(
        log_pop_dens = log1p(pop_dens),
        log_build_h = log1p(build_h),
        log_build_v = log1p(build_v),
        t2m_c = t2m - mean(t2m, na.rm = TRUE)
      )
  }

  # Ensure city_point_id exists
  if (!"city_point_id" %in% names(panel_df)) {
    panel_df$city_point_id <- paste(panel_df$city, panel_df$point_id, sep = "_")
  }

  cat("Panel size: ", format(nrow(panel_df), big.mark=","), " observations\n\n")

  models <- list()

  # Quadratic baseline (comparable to spline - same controls, no interactions)
  cat("--- Fitting quadratic baseline ---\n")
  tryCatch({
    models$quad_base <- feols(
      lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation |
        ym + lcz,
      data = panel_df, cluster = ~city_point_id
    )
    cat("  Quadratic baseline fitted\n")
  }, error = function(e) warning("Quadratic baseline failed: ", e$message))

  # Spline baseline (same controls/FE)
  cat("--- Fitting spline baseline (df=4) ---\n")
  tryCatch({
    models$spline_base <- feols(
      lst ~ ns(t2m, df = 4) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation |
        ym + lcz,
      data = panel_df, cluster = ~city_point_id
    )
    cat("  Spline baseline fitted\n")
  }, error = function(e) warning("Spline baseline failed: ", e$message))

  # Keep the richer quadratic interaction model as a separate benchmark
  cat("--- Fitting quadratic + interactions benchmark ---\n")
  tryCatch({
    models$quad_interact <- feols(
      lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
        t2m_c:out_b + t2m_c:water + t2m_c:lcz | ym + lcz,
      data = panel_df, cluster = ~city_point_id
    )
    cat("  Quadratic interaction model fitted\n")
  }, error = function(e) warning("Quadratic interaction model failed: ", e$message))

  # Evaluate all models
  cat("\n--- IN-SAMPLE EVALUATION ---\n\n")

  eval_results <- bind_rows(lapply(names(models), function(nm) {
    if (!is.null(models[[nm]])) {
      eval_model_on_data(models[[nm]], panel_df, nm)
    }
  }))

  print(eval_results)

  # Compare quadratic vs spline
  cat("\n--- COMPARISON SUMMARY ---\n\n")

  if ("quad_base" %in% eval_results$model && "spline_base" %in% eval_results$model) {
    quad <- eval_results %>% filter(model == "quad_base")
    spline <- eval_results %>% filter(model == "spline_base")

    cat("Quadratic (baseline) vs Spline (df=4):\n")
    cat(sprintf("  RMSE:     %.3f vs %.3f (diff: %.3f)\n", quad$rmse, spline$rmse, quad$rmse - spline$rmse))
    cat(sprintf("  MAE:      %.3f vs %.3f (diff: %.3f)\n", quad$mae, spline$mae, quad$mae - spline$mae))
    cat(sprintf("  R²:       %.4f vs %.4f (diff: %.4f)\n", quad$r2_pred, spline$r2_pred, spline$r2_pred - quad$r2_pred))
    cat(sprintf("  RMSE hot: %.3f vs %.3f (diff: %.3f)\n", quad$rmse_hot, spline$rmse_hot, quad$rmse_hot - spline$rmse_hot))
    cat(sprintf("  MAE hot:  %.3f vs %.3f (diff: %.3f)\n", quad$mae_hot, spline$mae_hot, quad$mae_hot - spline$mae_hot))

    # Interpret
    cat("\n--- INTERPRETATION ---\n")
    rmse_gain <- quad$rmse - spline$rmse
    rmse_hot_gain <- quad$rmse_hot - spline$rmse_hot

    if (rmse_gain > 0.05 || rmse_hot_gain > 0.1) {
      cat("  -> SPLINE provides meaningful improvement\n")
    } else if (rmse_gain > 0) {
      cat("  -> Spline provides marginal improvement; quadratic may be sufficient\n")
    } else {
      cat("  -> Quadratic performs equally well or better; prefer simpler model\n")
    }
  }

  # Save results (Rome uses full panel, mark as such)
  eval_file <- file.path(VALIDATION_DIR, "rome_spline_vs_quadratic_full.csv")
  write.csv(eval_results, eval_file, row.names = FALSE)
  cat("\nSaved: ", eval_file, "\n")

  return(list(models = models, evaluation = eval_results))
}

# STEP 1: MULTICITY SUBSET WITH IN-SAMPLE EVALUATION

run_multicity_insample <- function(cities = VALIDATION_CITIES) {

  cat("\n")
  cat("================================================================================\n")
  cat("  STEP 1: MULTICITY IN-SAMPLE EVALUATION\n")
  cat("  Cities: ", paste(cities, collapse = ", "), "\n")
  cat("  LST = daily ", toupper(LST_STAT), " | T2M = daily ", toupper(T2M_STAT), "\n")
  if (!is.null(VALIDATION_MAX_POINTS)) {
    cat("  Point cap per city: ", VALIDATION_MAX_POINTS, "\n")
  }
  cat("================================================================================\n\n")

  all_panels <- list()
  all_eval <- data.frame()

  for (city in cities) {
    cat("\n========== ", city, " ==========\n")

    tryCatch({
      panel_df <- load_or_build_city_panel(city, years = 2008:2017)

      if (!is.null(panel_df)) {
        all_panels[[city]] <- panel_df

        # Ensure city_point_id exists
        if (!"city_point_id" %in% names(panel_df)) {
          panel_df$city_point_id <- paste(panel_df$city, panel_df$point_id, sep = "_")
        }

        # Fit models
        models <- list()

        # Model 0: T2M only
        tryCatch({
          models$m0 <- feols(lst ~ t2m, data = panel_df, cluster = ~city_point_id)
        }, error = function(e) NULL)

        # Model 1: + ym FE
        tryCatch({
          models$m1 <- feols(lst ~ t2m | ym, data = panel_df, cluster = ~city_point_id)
        }, error = function(e) NULL)

        # Model 2: + controls + lcz FE
        tryCatch({
          models$m2 <- feols(
            lst ~ t2m + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | ym + lcz,
            data = panel_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Model 3: Quadratic + interactions
        tryCatch({
          models$m3 <- feols(
            lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
              t2m_c:out_b + t2m_c:water + t2m_c:lcz | ym + lcz,
            data = panel_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Model 4: Spline
        tryCatch({
          models$m4_spline <- feols(
            lst ~ ns(t2m, df = 4) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | ym + lcz,
            data = panel_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Evaluate
        pretty_names <- c(m0 = "m0_t2m", m1 = "m1_ymFE", m2 = "m2_controls",
                          m3 = "m3_quadratic", m4_spline = "m4_spline")

        city_eval <- bind_rows(lapply(names(models), function(nm) {
          if (!is.null(models[[nm]])) {
            res <- eval_model_on_data(models[[nm]], panel_df, pretty_names[[nm]])
            res$city <- city
            res
          }
        }))

        all_eval <- rbind(all_eval, city_eval)

        cat("  Models fitted: ", length(models), "\n")
      }
    }, error = function(e) {
      warning("Failed for ", city, ": ", e$message)
    })
  }

  # Summary
  cat("\n")
  cat("================================================================================\n")
  cat("  MULTICITY IN-SAMPLE SUMMARY\n")
  cat("================================================================================\n\n")

  summary_by_model <- all_eval %>%
    group_by(model) %>%
    summarise(
      n_cities = n(),
      mean_rmse = round(mean(rmse, na.rm = TRUE), 3),
      mean_mae = round(mean(mae, na.rm = TRUE), 3),
      mean_r2 = round(mean(r2_pred, na.rm = TRUE), 4),
      mean_rmse_hot = round(mean(rmse_hot, na.rm = TRUE), 3),
      .groups = "drop"
    )

  print(summary_by_model)

  # Save with sample info in filename
  eval_file <- file.path(VALIDATION_DIR, validation_filename("multicity_insample_evaluation"))
  write.csv(all_eval, eval_file, row.names = FALSE)
  cat("\nSaved: ", eval_file, "\n")

  summary_file <- file.path(VALIDATION_DIR, validation_filename("multicity_insample_summary"))
  write.csv(summary_by_model, summary_file, row.names = FALSE)
  cat("Saved: ", summary_file, "\n")

  return(list(panels = all_panels, evaluation = all_eval, summary = summary_by_model))
}

# STEP 2: WITHIN-CITY TEMPORAL HOLDOUT

run_temporal_holdout <- function(cities = VALIDATION_CITIES) {

  cat("\n")
  cat("================================================================================\n")
  cat("  STEP 2: WITHIN-CITY TEMPORAL HOLDOUT\n")
  cat("  Train: ", min(TRAIN_YEARS), "-", max(TRAIN_YEARS), "\n")
  cat("  Test:  ", min(TEST_YEARS), "-", max(TEST_YEARS), "\n")
  if (!is.null(VALIDATION_MAX_POINTS)) {
    cat("  Point cap per city: ", VALIDATION_MAX_POINTS, "\n")
  }
  cat("================================================================================\n\n")

  cat("NOTE: Using HOLDOUT-SAFE specification with factor(month) instead of ym FE.\n")
  cat("      ym FE cannot extrapolate to unseen year-month combinations.\n")
  cat("      Spline knots computed from TRAINING data only.\n\n")

  all_eval <- data.frame()

  for (city in cities) {
    cat("\n========== ", city, " ==========\n")

    tryCatch({
      # Load cached panel
      panel_df <- load_or_build_city_panel(city, years = 2008:2017)

      if (!is.null(panel_df)) {
        # Ensure city_point_id exists
        if (!"city_point_id" %in% names(panel_df)) {
          panel_df$city_point_id <- paste(panel_df$city, panel_df$point_id, sep = "_")
        }

        # Split
        train_df <- panel_df %>% filter(year %in% TRAIN_YEARS)
        test_df <- panel_df %>% filter(year %in% TEST_YEARS)

        # Re-center T2M on training data
        train_mean_t2m <- mean(train_df$t2m, na.rm = TRUE)
        train_df$t2m_c <- train_df$t2m - train_mean_t2m
        test_df$t2m_c <- test_df$t2m - train_mean_t2m

        # Compute spline knots from TRAINING data only (honest holdout)
        spline_knots <- compute_spline_knots(train_df$t2m, df = 4)
        train_df <- add_spline_columns(train_df, spline_knots)
        test_df <- add_spline_columns(test_df, spline_knots)

        # Sanity check spline basis
        spline_cols <- paste0("spline_", 1:4)
        stopifnot(all(spline_cols %in% names(train_df)))
        stopifnot(all(spline_cols %in% names(test_df)))

        cat("  Train: ", format(nrow(train_df), big.mark=","), " obs\n")
        cat("  Test:  ", format(nrow(test_df), big.mark=","), " obs\n")
        cat("  Spline knots (from train): ", paste(round(spline_knots$knots, 1), collapse=", "), "\n")

        # Fit HOLDOUT-SAFE models on training data
        # Using factor(month) instead of ym FE
        models <- list()

        # Model 0: T2M only
        tryCatch({
          models$m0 <- feols(lst ~ t2m, data = train_df, cluster = ~city_point_id)
        }, error = function(e) NULL)

        # Model 1: + month FE (holdout-safe)
        tryCatch({
          models$m1_month <- feols(lst ~ t2m | month, data = train_df, cluster = ~city_point_id)
        }, error = function(e) NULL)

        # Model 2: + controls + lcz FE (holdout-safe)
        tryCatch({
          models$m2_month <- feols(
            lst ~ t2m + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | month + lcz,
            data = train_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Model 3: Quadratic + interactions (holdout-safe)
        tryCatch({
          models$m3_quad <- feols(
            lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
              t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
            data = train_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Model 3b: Quadratic + interactions + harmonic seasonality
        tryCatch({
          models$m3_quad_month_harm <- feols(
            lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
              sin_doy + cos_doy +
              t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
            data = train_df, cluster = ~city_point_id
          )
        }, error = function(e) {
          message("  m3_quad_month_harm FAILED for ", city, ": ", conditionMessage(e))
          NULL
        })
        
        # Model 4: Spline (holdout-safe with training-defined knots)
        tryCatch({
          models$m4_spline <- feols(
            lst ~ spline_1 + spline_2 + spline_3 + spline_4 +
              out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | month + lcz,
            data = train_df, cluster = ~city_point_id
          )
        }, error = function(e) NULL)

        # Evaluate on TEST set
        pretty_names <- c(
          m0 = "m0_t2m",
          m1_month = "m1_monthFE",
          m2_month = "m2_controls",
          m3_quad = "m3_quadratic",
          m3_quad_month_harm = "m3_quadratic_month_harm",
          m4_spline = "m4_spline"
        )

        city_eval <- bind_rows(lapply(names(models), function(nm) {
          if (!is.null(models[[nm]])) {
            res <- eval_model_on_data(models[[nm]], test_df, pretty_names[[nm]])
            res$city <- city
            res$split <- "test"
            res
          }
        }))

        all_eval <- rbind(all_eval, city_eval)

        cat("  Test set evaluation complete\n")
      }
    }, error = function(e) {
      warning("Failed for ", city, ": ", e$message)
    })
  }

  # Summary
  cat("\n")
  cat("================================================================================\n")
  cat("  TEMPORAL HOLDOUT SUMMARY (TEST SET ONLY)\n")
  cat("================================================================================\n\n")

  summary_by_model <- all_eval %>%
    group_by(model) %>%
    summarise(
      n_cities = n(),
      mean_rmse = round(mean(rmse, na.rm = TRUE), 3),
      mean_mae = round(mean(mae, na.rm = TRUE), 3),
      mean_r2 = round(mean(r2_pred, na.rm = TRUE), 4),
      mean_rmse_hot = round(mean(rmse_hot, na.rm = TRUE), 3),
      .groups = "drop"
    )

  print(summary_by_model)

  # Save with sample info in filename
  eval_file <- file.path(VALIDATION_DIR, validation_filename("temporal_holdout_evaluation"))
  write.csv(all_eval, eval_file, row.names = FALSE)
  cat("\nSaved: ", eval_file, "\n")

  summary_file <- file.path(VALIDATION_DIR, validation_filename("temporal_holdout_summary"))
  write.csv(summary_by_model, summary_file, row.names = FALSE)
  cat("Saved: ", summary_file, "\n")

  return(list(evaluation = all_eval, summary = summary_by_model))
}

# STEP 3: CITY-HOLDOUT (LEAVE-ONE-OUT)

run_city_holdout <- function(cities = VALIDATION_CITIES) {

  cat("\n")
  cat("================================================================================\n")
  cat("  STEP 3: CITY-HOLDOUT (LEAVE-ONE-CITY-OUT)\n")
  cat("  Cities: ", paste(cities, collapse = ", "), "\n")
  if (!is.null(VALIDATION_MAX_POINTS)) {
    cat("  Point cap per city: ", VALIDATION_MAX_POINTS, "\n")
  }
  cat("================================================================================\n\n")

  cat("NOTE: Using HOLDOUT-SAFE specification with factor(month) for portability.\n")
  cat("      Spline knots computed from TRAINING cities only.\n\n")

  # First, build all panels (using cache)
  cat("Loading/building panels for all cities...\n")
  all_panels <- list()

  for (city in cities) {
    tryCatch({
      panel_df <- load_or_build_city_panel(city, years = 2008:2017)
      if (!is.null(panel_df)) {
        # Ensure city_point_id exists
        if (!"city_point_id" %in% names(panel_df)) {
          panel_df$city_point_id <- paste(panel_df$city, panel_df$point_id, sep = "_")
        }
        all_panels[[city]] <- panel_df
      }
    }, error = function(e) {
      warning("Failed for ", city, ": ", e$message)
    })
  }

  available_cities <- names(all_panels)
  cat("\nAvailable cities: ", paste(available_cities, collapse = ", "), "\n\n")

  if (length(available_cities) < 2) {
    warning("Need at least 2 cities for city-holdout")
    return(NULL)
  }

  all_eval <- data.frame()

  # Leave-one-out validation
  for (held_out in available_cities) {
    cat("\n========== Holding out: ", held_out, " ==========\n")

    train_cities <- setdiff(available_cities, held_out)

    # Combine training data
    train_df <- bind_rows(all_panels[train_cities])
    test_df <- all_panels[[held_out]]

    # Re-center T2M on training data
    train_mean_t2m <- mean(train_df$t2m, na.rm = TRUE)
    train_df$t2m_c <- train_df$t2m - train_mean_t2m
    test_df$t2m_c <- test_df$t2m - train_mean_t2m

    # Compute spline knots from TRAINING data only (honest holdout)
    spline_knots <- compute_spline_knots(train_df$t2m, df = 4)
    train_df <- add_spline_columns(train_df, spline_knots)
    test_df <- add_spline_columns(test_df, spline_knots)

    # Sanity check spline basis
    spline_cols <- paste0("spline_", 1:4)
    stopifnot(all(spline_cols %in% names(train_df)))
    stopifnot(all(spline_cols %in% names(test_df)))

    cat("  Train cities: ", paste(train_cities, collapse = ", "), "\n")
    cat("  Train size: ", format(nrow(train_df), big.mark=","), " obs\n")
    cat("  Test size:  ", format(nrow(test_df), big.mark=","), " obs\n")

    # Fit models on training data (cross-city portable specification)
    models <- list()

    # Model 0: T2M only
    tryCatch({
      models$m0 <- feols(lst ~ t2m, data = train_df, cluster = ~city_point_id)
    }, error = function(e) NULL)

    # Model 1: + month FE
    tryCatch({
      models$m1_month <- feols(lst ~ t2m | month, data = train_df, cluster = ~city_point_id)
    }, error = function(e) NULL)

    # Model 2: + controls + lcz FE (no city FE - test portability)
    tryCatch({
      models$m2_month <- feols(
        lst ~ t2m + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | month + lcz,
        data = train_df, cluster = ~city_point_id
      )
    }, error = function(e) NULL)

    # Model 3: Quadratic + interactions
    tryCatch({
      models$m3_quad <- feols(
        lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
          t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
        data = train_df, cluster = ~city_point_id
      )
    }, error = function(e) NULL)
    
    # Model 3b: Quadratic + interactions + harmonic seasonality
    tryCatch({
      models$m3_quad_month_harm <- feols(
        lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
          sin_doy + cos_doy +
          t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
        data = train_df, cluster = ~city_point_id
      )
    }, error = function(e) {
      message("  m3_quad_month_harm FAILED for held-out ", held_out, ": ", conditionMessage(e))
      NULL
    })

    # Model 4: Spline (with training-defined knots)
    tryCatch({
      models$m4_spline <- feols(
        lst ~ spline_1 + spline_2 + spline_3 + spline_4 +
          out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | month + lcz,
        data = train_df, cluster = ~city_point_id
      )
    }, error = function(e) NULL)

    # Evaluate on held-out city
    pretty_names <- c(
      m0 = "m0_t2m",
      m1_month = "m1_monthFE",
      m2_month = "m2_controls",
      m3_quad = "m3_quadratic",
      m3_quad_month_harm = "m3_quadratic_month_harm",
      m4_spline = "m4_spline"
    )

    city_eval <- bind_rows(lapply(names(models), function(nm) {
      if (!is.null(models[[nm]])) {
        res <- eval_model_on_data(models[[nm]], test_df, pretty_names[[nm]])
        res$held_out_city <- held_out
        res
      }
    }))

    all_eval <- rbind(all_eval, city_eval)

    cat("  Held-out evaluation complete\n")
  }

  # Summary
  cat("\n")
  cat("================================================================================\n")
  cat("  CITY-HOLDOUT SUMMARY\n")
  cat("================================================================================\n\n")

  summary_by_model <- all_eval %>%
    group_by(model) %>%
    summarise(
      n_holdouts = n(),
      mean_rmse = round(mean(rmse, na.rm = TRUE), 3),
      mean_mae = round(mean(mae, na.rm = TRUE), 3),
      mean_r2 = round(mean(r2_pred, na.rm = TRUE), 4),
      mean_rmse_hot = round(mean(rmse_hot, na.rm = TRUE), 3),
      .groups = "drop"
    )

  print(summary_by_model)

  # By held-out city
  cat("\n--- By Held-Out City (quadratic model) ---\n\n")
  city_summary <- all_eval %>%
    filter(model == "m3_quadratic") %>%
    select(held_out_city, rmse, mae, r2_pred, rmse_hot)
  print(city_summary)

  # Save with sample info in filename
  eval_file <- file.path(VALIDATION_DIR, validation_filename("city_holdout_evaluation"))
  write.csv(all_eval, eval_file, row.names = FALSE)
  cat("\nSaved: ", eval_file, "\n")

  summary_file <- file.path(VALIDATION_DIR, validation_filename("city_holdout_summary"))
  write.csv(summary_by_model, summary_file, row.names = FALSE)
  cat("Saved: ", summary_file, "\n")

  return(list(evaluation = all_eval, summary = summary_by_model))
}

# STEP 4: FINAL SUMMARY AND RECOMMENDATIONS

generate_final_summary <- function(spline_result, insample_result, temporal_result, city_result) {

  cat("\n")
  cat("################################################################################\n")
  cat("#                     FINAL VALIDATION SUMMARY                                 #\n")
  cat("################################################################################\n\n")

  cat("METRIC CHOICE: LST daily ", toupper(LST_STAT), " | T2M daily ", toupper(T2M_STAT), "\n\n")

  # 1. Spline vs Quadratic
  cat("================================================================================\n")
  cat("1. SPLINE VS QUADRATIC (Rome)\n")
  cat("================================================================================\n\n")

  if (!is.null(spline_result)) {
    print(spline_result$evaluation)
    cat("\n")
  }

  # 2. In-sample multicity
  cat("================================================================================\n")
  cat("2. IN-SAMPLE MULTICITY\n")
  cat("================================================================================\n\n")

  if (!is.null(insample_result)) {
    print(insample_result$summary)
    cat("\n")
  }

  # 3. Temporal holdout
  cat("================================================================================\n")
  cat("3. TEMPORAL HOLDOUT (Train 2008-2015, Test 2016-2017)\n")
  cat("================================================================================\n\n")

  if (!is.null(temporal_result)) {
    print(temporal_result$summary)
    cat("\n")
  }

  # 4. City holdout
  cat("================================================================================\n")
  cat("4. CITY-HOLDOUT (Leave-One-City-Out)\n")
  cat("================================================================================\n\n")

  if (!is.null(city_result)) {
    print(city_result$summary)
    cat("\n")
  }

  # Recommendations
  cat("================================================================================\n")
  cat("INTERPRETATION AND RECOMMENDATIONS\n")
  cat("================================================================================\n\n")

  # Compare in-sample vs holdout
  if (!is.null(insample_result) && !is.null(temporal_result)) {
    insample_m3 <- insample_result$summary %>% filter(model == "m3_quadratic")
    temporal_m3 <- temporal_result$summary %>% filter(model == "m3_quadratic")

    if (nrow(insample_m3) > 0 && nrow(temporal_m3) > 0) {
      rmse_gap <- temporal_m3$mean_rmse - insample_m3$mean_rmse
      r2_gap <- insample_m3$mean_r2 - temporal_m3$mean_r2

      cat("Overfitting check (quadratic model):\n")
      cat(sprintf("  In-sample RMSE: %.3f | Temporal holdout RMSE: %.3f | Gap: %.3f\n",
                  insample_m3$mean_rmse, temporal_m3$mean_rmse, rmse_gap))
      cat(sprintf("  In-sample R²:   %.4f | Temporal holdout R²:   %.4f | Gap: %.4f\n",
                  insample_m3$mean_r2, temporal_m3$mean_r2, r2_gap))

      if (rmse_gap < 0.3 && r2_gap < 0.02) {
        cat("  -> Model generalizes well across time\n")
      } else if (rmse_gap < 0.5) {
        cat("  -> Moderate generalization gap; acceptable\n")
      } else {
        cat("  -> Large generalization gap; potential overfitting\n")
      }
      cat("\n")
    }
  }

  # City portability
  if (!is.null(city_result)) {
    city_m3 <- city_result$summary %>% filter(model == "m3_quadratic")

    if (nrow(city_m3) > 0) {
      cat("City portability (quadratic model):\n")
      cat(sprintf("  Cross-city RMSE: %.3f | R²: %.4f\n",
                  city_m3$mean_rmse, city_m3$mean_r2))

      if (city_m3$mean_r2 > 0.85) {
        cat("  -> Emulator transfers well across cities\n")
      } else if (city_m3$mean_r2 > 0.75) {
        cat("  -> Moderate transfer; may need city-specific adjustments\n")
      } else {
        cat("  -> Poor transfer; city-specific models may be needed\n")
      }
      cat("\n")
    }
  }

  # Spline vs quadratic
  if (!is.null(spline_result)) {
    quad <- spline_result$evaluation %>% filter(model == "quad_base")
    spline <- spline_result$evaluation %>% filter(model == "spline_base")

    if (nrow(quad) > 0 && nrow(spline) > 0) {
      rmse_diff <- quad$rmse - spline$rmse
      hot_diff <- quad$rmse_hot - spline$rmse_hot

      cat("Spline vs Quadratic recommendation:\n")
      if (rmse_diff > 0.1 || hot_diff > 0.15) {
        cat("  -> Spline provides meaningful improvement; consider switching\n")
      } else {
        cat("  -> Quadratic is sufficient; prefer simpler model\n")
      }
      cat("\n")
    }
  }

  # Final recommendation
  cat("================================================================================\n")
  cat("FINAL RECOMMENDATION\n")
  cat("================================================================================\n\n")

  cat("Based on validation results:\n\n")
  cat("1. COMPLEXITY: Check if quadratic specification is sufficient.\n")
  cat("   If spline gain < 0.1 RMSE, prefer simpler quadratic.\n\n")
  cat("2. GENERALIZATION: Check temporal and city holdout R² values.\n")
  cat("   If both > 0.85, the emulator is robust.\n\n")
  cat("3. NEXT STEPS:\n")
  cat("   - If validation looks good, proceed to full multicity scaling.\n")
  cat("   - Consider weekly aggregation for hazard support alignment.\n")
  cat("   - Keep quadratic for now; revisit spline if hot-tail matters more.\n\n")
}

#' Save final summary to file
save_final_summary <- function(spline_result, insample_result, temporal_result, city_result) {
  summary_file <- file.path(VALIDATION_DIR, validation_filename("final_validation_summary", "txt"))
  txt <- capture.output(
    generate_final_summary(spline_result, insample_result, temporal_result, city_result)
  )
  writeLines(txt, summary_file)
  cat("Saved: ", summary_file, "\n")
}

# RUN FULL VALIDATION PIPELINE
# Only run when script is executed directly, not when sourced

if (sys.nframe() == 0) {
  cat("\n\n")
  cat("################################################################################\n")
  cat("#         CLIMATE EMULATOR VALIDATION PIPELINE                                 #\n")
  cat("#         LST = daily MAX | T2M = daily MEAN                                   #\n")
  cat("################################################################################\n\n")

  # Step 0: Rome spline comparison
  spline_result <- run_rome_spline_comparison()

  # Step 1: Multicity in-sample
  insample_result <- run_multicity_insample(VALIDATION_CITIES)

  # Step 2: Temporal holdout
  temporal_result <- run_temporal_holdout(VALIDATION_CITIES)

  # Step 3: City holdout
  city_result <- run_city_holdout(VALIDATION_CITIES)

  # Step 4: Final summary
  generate_final_summary(spline_result, insample_result, temporal_result, city_result)
  save_final_summary(spline_result, insample_result, temporal_result, city_result)

  cat("\n\n[DONE] Validation pipeline completed.\n")
  cat("Outputs saved to: ", VALIDATION_DIR, "\n")
}
