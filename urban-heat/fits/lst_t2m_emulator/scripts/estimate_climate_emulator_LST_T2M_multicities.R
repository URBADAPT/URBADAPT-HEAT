# estimate_climate_emulator_LST_T2M_multicities.R
# Climate emulator: Bridge T2M (air temperature) to LST (surface temperature)
# using local urban features
#
# PURPOSE:
# Build a statistical bridge linking T2M and LST using:
# - T2M as main predictor (matched point/day)
# - Local controls: LCZ, GVI, build_h, build_v, pop_dens, water, elevation
# - Time controls: month FE (holdout-safe)
#
# VERSION: Supercomputer — 142 PROVIDE cities, full points (no sampling)
#
# METRIC CHOICE (EXPLICIT):
# This emulator uses:
#   - LST = daily MAXIMUM land surface temperature
#   - T2M = daily MEAN air temperature at 2m
#
# File patterns:
#   - LST: LST_year_daily_max_<year>.nc
#   - T2M: T2M_year_daily_mean_<year>.nc
#
# DATA STRUCTURE:
# - T2M files: data/T2M/<city>/T2M_year_daily_mean_<year>.nc
#   - Variable: "T2M" (units: Kelvin, scale_factor: 0.01)
#   - Same grid as LST (e.g., Rome: 301x301, EPSG:3035)
# - LST files: data/LST/<city>/LST_year_daily_max_<year>.nc
#   - Variable: "LST" (units: Kelvin, scale_factor: 0.01)
# - Both grids align exactly, so direct point extraction works
#

if (Sys.info()["sysname"] == "Darwin") {
  Sys.setenv(PROJ_LIB = "/opt/homebrew/opt/proj/share/proj")
}

suppressPackageStartupMessages({
  library(terra)
  library(sf)
  library(dplyr)
  library(tidyr)
  library(data.table)   # used for memory-efficient panel construction
  library(fixest)
  library(ncdf4)
  library(httr)
  library(elevatr)
})

terra::gdalCache(4000)

# CONFIGURATION

# METRIC CHOICE - change these to use different variants
LST_STAT <- "max"   # "max" or "mean"
T2M_STAT <- "mean"  # "mean" (T2M typically only has mean available)

YEARS <- 2008:2017
DATA_DIR_LST <- "data/LST"
DATA_DIR_T2M <- "data/T2M"
OUTPUT_DIR   <- "outputs/emulator"

# ── LOCAL MIRROR ──────────────────────────────────────────────────────────────
# If .nc files already exist anywhere under this directory tree they are used
# directly, without copying and without any download attempt.
# Set to NULL (or "") to disable mirror look-up entirely.
LOCAL_MIRROR_DIR <- "H:/ECIP/Falchetta/Downloads/compressed_daily"
# ─────────────────────────────────────────────────────────────────────────────

dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

# CITY MAPPING: PROVIDE name -> GVI name
CITY_MAPPING <- list(
  "Addis Abeba"       = "Addis Ababa",
  "Teheran"           = "Tehran",
  "Belgrado"          = "Belgrade",
  "Tschwane"          = "Pretoria",
  "Sevilla"           = "Seville",
  "Goteborg"          = "Gothenburg",
  "Ho Chi Minh"       = "Ho Chi Minh City",
  "Rotterdam"         = "Rotterdam [The Hague]",
  "Islamabad"         = "Rawalpindi [Islamabad]",
  "Liege"             = "Liège",
  "Kosice"            = "Košice",
  "Cluj Napoca"       = "Cluj-Napoca",
  "Gyor"              = "Győr",
  "Pecs"              = "Pécs",
  "Trieste"           = "Triest",
  "Alicante"          = "Alacant / Alicante",
  "Malaga"            = "Málaga",
  "Klaipeda"          = "Klaipėda",
  "Frankfurt Am Main" = "Frankfurt am Main",
  "Palma De Mallorca" = "Palma de Mallorca"
)

# HELPER FUNCTIONS

#' Safe conversion to Celsius
to_celsius_safe <- function(x) {
  max_val <- max(x, na.rm = TRUE)
  if (max_val > 1000) {
    message("    Applying scale_factor (values appear packed)...")
    x <- x * 0.01
  }
  max_val <- max(x, na.rm = TRUE)
  if (max_val > 150) {
    message("    Converting Kelvin to Celsius...")
    x <- x - 273.15
  }
  return(x)
}

# ── NEW HELPER ────────────────────────────────────────────────────────────────
#' Search the local mirror tree for a specific .nc file.
#'
#' The mirror is expected to follow the same layout as the PROVIDE FTP tree:
#'   <LOCAL_MIRROR_DIR>/<city_with_underscores>/<filename>
#' but this function also performs a recursive fallback (list.files recursive)
#' so it will find the file even if it sits in an unexpected sub-folder.
#'
#' @param filename  Bare file name, e.g. "LST_year_daily_max_2010.nc"
#' @param city_provide  PROVIDE city name (spaces replaced with underscores
#'                      when constructing the expected sub-folder)
#' @return Full path to the file if found and non-empty, otherwise NULL.
find_in_mirror <- function(filename, city_provide) {
  if (is.null(LOCAL_MIRROR_DIR) || !nzchar(LOCAL_MIRROR_DIR)) return(NULL)
  if (!dir.exists(LOCAL_MIRROR_DIR)) return(NULL)
  
  city_slug <- gsub(" ", "_", city_provide)
  
  # 1. Try the canonical path first (fast)
  canonical <- file.path(LOCAL_MIRROR_DIR, city_slug, filename)
  if (file.exists(canonical) && file.size(canonical) > 10000) {
    return(canonical)
  }
  
  # 2. Recursive search across all sub-folders (handles non-standard layouts)
  hits <- list.files(
    path       = LOCAL_MIRROR_DIR,
    pattern    = paste0("^", gsub("\\.", "\\\\.", filename), "$"),
    recursive  = TRUE,
    full.names = TRUE
  )
  # Keep only non-empty files
  hits <- hits[file.exists(hits) & file.size(hits) > 10000]
  
  if (length(hits) == 0) return(NULL)
  
  # Prefer a hit whose parent folder name matches the city slug
  city_match <- hits[grepl(city_slug, hits, ignore.case = TRUE)]
  if (length(city_match) > 0) return(city_match[1])
  
  return(hits[1])   # fall back to first valid hit
}
# ─────────────────────────────────────────────────────────────────────────────

#' Download PROVIDE climate data file (only if missing or corrupted).
#' Priority order:
#'   1. Local mirror  (H:/ECIP/… tree)  – used in-place, no copy
#'   2. Working cache (data/LST|T2M/<city>/)
#'   3. Remote PROVIDE API download -> saved to working cache
#'
#' @param city_provide City name as used by PROVIDE
#' @param year Year
#' @param stat Statistic: "max" or "mean"
#' @param data_type "LST" or "T2M"
download_provide_file <- function(city_provide, year, stat = "mean", data_type = "LST") {
  base_url <- "https://provide.marvin.vito.be/ftp/compressed_daily"
  
  if (data_type == "LST") {
    filename  <- sprintf("LST_year_daily_%s_%d.nc", stat, year)
    local_dir <- file.path(DATA_DIR_LST, city_provide)
  } else if (data_type == "T2M") {
    filename  <- sprintf("T2M_year_daily_%s_%d.nc", stat, year)
    local_dir <- file.path(DATA_DIR_T2M, city_provide)
  } else {
    stop("data_type must be 'LST' or 'T2M'")
  }
  
  local_path <- file.path(local_dir, filename)
  
  # ── PRIORITY 1: local mirror ──────────────────────────────────────────────
  mirror_path <- find_in_mirror(filename, city_provide)
  if (!is.null(mirror_path)) {
    message("    [", year, "] Using mirror file: ", mirror_path)
    return(mirror_path)   # use in-place — no copy, no download
  }
  
  # ── PRIORITY 2: working cache ─────────────────────────────────────────────
  if (file.exists(local_path) && file.size(local_path) > 10000) {
    message("    [", year, "] Using cached file: ", basename(local_path))
    return(local_path)
  }
  
  # ── PRIORITY 3: remote download ───────────────────────────────────────────
  dir.create(local_dir, showWarnings = FALSE, recursive = TRUE)
  
  city_url <- gsub(" ", "_", city_provide)
  url      <- sprintf("%s/%s/%s", base_url, city_url, filename)
  
  message("    [", year, "] Downloading (not found locally): ", basename(filename), "...")
  resp <- tryCatch(
    GET(url, write_disk(local_path, overwrite = TRUE), progress(), timeout(600)),
    error = function(e) {
      warning("Download error for ", city_provide, " ", year, ": ", e$message)
      return(NULL)
    }
  )
  
  if (is.null(resp) || http_error(resp)) {
    if (file.exists(local_path)) unlink(local_path)
    stop("Failed to download: ", url)
  }
  
  Sys.sleep(0.5)
  return(local_path)
}

#' Load NetCDF raster stack with proper CRS and extent
load_nc_stack <- function(nc_files) {
  nc     <- nc_open(nc_files[1])
  x_vals <- ncvar_get(nc, "x")
  y_vals <- ncvar_get(nc, "y")
  nc_text  <- capture.output(print(nc))
  epsg_city <- as.integer(gsub("[^0-9]", "", grep("EPSG", nc_text, value = TRUE)[1]))
  nc_close(nc)
  
  dx   <- abs(mean(diff(x_vals))); dy <- abs(mean(diff(y_vals)))
  xmin <- min(x_vals) - dx/2;     xmax <- max(x_vals) + dx/2
  ymin <- min(y_vals) - dy/2;     ymax <- max(y_vals) + dy/2
  
  rast_list <- lapply(nc_files, function(f) {
    r      <- rast(f)
    ext(r) <- c(xmin, xmax, ymin, ymax)
    crs(r) <- paste0("EPSG:", epsg_city)
    return(r)
  })
  
  list(stack = rast(rast_list), epsg = epsg_city)
}

#' Model evaluation
eval_model <- function(model, data, name = "model") {
  pred <- predict(model, newdata = data)
  ok   <- is.finite(pred) & is.finite(data$lst)
  y    <- data$lst[ok]; yhat <- pred[ok]; err <- y - yhat
  
  rmse    <- sqrt(mean(err^2))
  mae     <- mean(abs(err))
  r2_pred <- 1 - sum((y - yhat)^2) / sum((y - mean(y))^2)
  
  q90      <- quantile(y, 0.9, na.rm = TRUE); hot <- y >= q90
  rmse_hot <- sqrt(mean((y[hot] - yhat[hot])^2))
  mae_hot  <- mean(abs(y[hot] - yhat[hot]))
  
  data.frame(model = name, n = length(y),
             rmse = round(rmse, 3), mae = round(mae, 3),
             r2_pred = round(r2_pred, 4),
             rmse_hot = round(rmse_hot, 3), mae_hot = round(mae_hot, 3))
}

# MAIN EMULATOR FUNCTION  (unchanged below — calls download_provide_file which
# now handles the mirror look-up transparently)

estimate_climate_emulator <- function(city_provide, years = 2008:2017, sample_frac = 1) {
  
  city_gvi <- CITY_MAPPING[[city_provide]]
  if (is.null(city_gvi)) city_gvi <- city_provide
  
  message("\n================================================================")
  message("  CLIMATE EMULATOR: T2M -> LST")
  message("  PROVIDE city: ", city_provide)
  message("  GVI city:     ", city_gvi)
  message("  Years: ", min(years), "-", max(years))
  message("================================================================")
  message("  METRIC CHOICE:")
  message("    LST = daily ", toupper(LST_STAT), " land surface temperature")
  message("    T2M = daily ", toupper(T2M_STAT), " air temperature at 2m")
  message("================================================================\n")
  
  # 1. LOAD GVI DATA
  message("[1] Loading GVI data...")
  gvi_all <- readRDS("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/ugs/after_points_100425_citynames.rds")
  gvi_agg <- gvi_all %>%
    filter(city == city_gvi) %>%
    group_by(x, y) %>%
    summarise(out_b = mean(out_b, na.rm = TRUE), .groups = "drop")
  
  if (nrow(gvi_agg) == 0) {
    warning("No GVI points found for city: ", city_gvi,
            " (PROVIDE name: ", city_provide, ")")
    return(NULL)
  }
  gvi_agg$out_b <- gvi_agg$out_b / 100
  message("  GVI points: ", nrow(gvi_agg))
  
  # 2. CHECK AND LOAD DATA FILES (mirror → cache → download)
  message("\n[2] Checking data files (mirror → cache → download)...")
  message("  Looking for LST ", toupper(LST_STAT), " and T2M ", toupper(T2M_STAT), " files...")
  
  lst_files <- sapply(years, function(y) download_provide_file(city_provide, y, LST_STAT, "LST"))
  t2m_files <- sapply(years, function(y) download_provide_file(city_provide, y, T2M_STAT, "T2M"))
  
  lst_ok <- file.exists(lst_files) & file.size(lst_files) > 10000
  t2m_ok <- file.exists(t2m_files) & file.size(t2m_files) > 10000
  
  if (!all(lst_ok) || !all(t2m_ok)) {
    warning("Missing data files for ", city_provide)
    return(NULL)
  }
  
  message("  LST files: ", sum(lst_ok), " (", toupper(LST_STAT), ")")
  message("  T2M files: ", sum(t2m_ok), " (", toupper(T2M_STAT), ")")
  
  # 3. LOAD RASTER STACKS
  message("\n[3] Loading raster stacks...")
  lst_data  <- load_nc_stack(lst_files)
  lst_stack <- lst_data$stack; epsg_city <- lst_data$epsg
  message("  LST layers: ", nlyr(lst_stack), " (", toupper(LST_STAT), ")")
  
  t2m_data  <- load_nc_stack(t2m_files)
  t2m_stack <- t2m_data$stack
  message("  T2M layers: ", nlyr(t2m_stack), " (", toupper(T2M_STAT), ")")
  
  message("\n  Checking grid alignment...")
  geom_ok <- terra::compareGeom(lst_stack[[1]], t2m_stack[[1]], stopOnError = FALSE)
  if (!isTRUE(geom_ok)) stop("FATAL: LST and T2M grids do not align exactly.")
  message("  Grid alignment: OK")
  
  if (nlyr(lst_stack) != nlyr(t2m_stack)) {
    n_layers  <- min(nlyr(lst_stack), nlyr(t2m_stack))
    lst_stack <- lst_stack[[1:n_layers]]
    t2m_stack <- t2m_stack[[1:n_layers]]
    message("  WARNING: layer count mismatch — using first ", n_layers, " layers")
  }
  
  # 4. ADD COVARIATES
  message("\n[4] Adding covariates...")
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)
  
  lcz_rast  <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/climate/lcz/lcz_filter_v3.tif")
  gvi_agg$lcz <- terra::extract(lcz_rast, vect(pts_wgs))[[2]]
  
    build_h  <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif")
  build_v  <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_V_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  pop_dens <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_POP_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  water    <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/climate/provide_urban_climate_data/c_gls_WB300_202306010000_GLOBE_S2_V2.0.1.nc", lyrs = "WB")
  
  gvi_agg$build_h  <- terra::extract(build_h,  vect(pts_wgs))[, 2]
  gvi_agg$build_v  <- terra::extract(build_v,  vect(pts_wgs))[, 2]
  gvi_agg$pop_dens <- terra::extract(pop_dens, vect(pts_wgs))[, 2]
  gvi_agg$water    <- terra::extract(water,    vect(pts_wgs))[, 2]
  
  for (col in c("build_h", "build_v", "pop_dens", "water"))
    gvi_agg[[col]][is.na(gvi_agg[[col]])] <- 0
  
  message("  Extracting elevation...")
  gvi_agg$elevation <- 0
  tryCatch({
    elev_pts <- get_elev_point(pts_wgs, prj = 4326, src = "aws", z = 10)
    gvi_agg$elevation <- elev_pts$elevation
    gvi_agg$elevation[is.na(gvi_agg$elevation)] <- 0
  }, error = function(e) warning("Elevation extraction failed: ", e$message))
  
  gvi_agg <- gvi_agg %>% filter(lcz >= 1 & lcz <= 10)
  message("  Points after LCZ 1-10 filter: ", nrow(gvi_agg))
  if (nrow(gvi_agg) < 100) { warning("Too few points for ", city_gvi); return(NULL) }
  
  # 5. EXTRACT LST AND T2M AT POINTS
  message("\n[5] Extracting LST (", toupper(LST_STAT), ") and T2M (",
          toupper(T2M_STAT), ") at points (daily)...")
  
  pts_wgs  <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)
  pts_proj <- st_transform(pts_wgs, epsg_city)
  
  message("  Extracting LST ", toupper(LST_STAT), "...")
  lst_mat <- as.matrix(terra::extract(lst_stack, vect(pts_proj))[, -1])
  
  message("  Extracting T2M ", toupper(T2M_STAT), "...")
  t2m_mat <- as.matrix(terra::extract(t2m_stack, vect(pts_proj))[, -1])
  
  message("  Converting LST to Celsius..."); lst_mat <- to_celsius_safe(lst_mat)
  message("  Converting T2M to Celsius..."); t2m_mat <- to_celsius_safe(t2m_mat)
  
  message("  LST range: ", round(min(lst_mat, na.rm=TRUE),1), " to ",
          round(max(lst_mat, na.rm=TRUE),1), " C")
  message("  T2M range: ", round(min(t2m_mat, na.rm=TRUE),1), " to ",
          round(max(t2m_mat, na.rm=TRUE),1), " C")
  
  # 6. BUILD DAILY PANEL
  message("\n[6] Building daily panel...")
  n_days <- ncol(lst_mat)
  dates  <- as.Date(unlist(lapply(sort(years), function(y)
    as.character(seq(as.Date(paste0(y,"-01-01")), as.Date(paste0(y,"-12-31")), "day")))))
  dates  <- dates[1:min(length(dates), n_days)]
  
  if (sample_frac < 1) {
    n_sample <- ceiling(length(dates) * sample_frac)
    day_idx  <- sort(sample(seq_along(dates), n_sample))
    dates    <- dates[day_idx]
    lst_mat  <- lst_mat[, day_idx, drop = FALSE]
    t2m_mat  <- t2m_mat[, day_idx, drop = FALSE]
    message("  Sampled ", n_sample, " days (", round(sample_frac*100), "%)")
  }
  
  n_points <- nrow(gvi_agg)
  n_days   <- length(dates)
  message("  Points: ", n_points, ", Days: ", n_days)
  message("  Building panel (", format(n_points * n_days, big.mark=","), " obs)...")

  # Build the panel as a data.table so subsequent column adds happen in place
  # (no dplyr/data.frame copy). Vectors are constructed once and consumed by
  # setDT, then the big raster matrices are freed before we go any further.
  panel_df <- data.table::data.table(
    city     = city_provide,
    point_id = rep(seq_len(n_points), n_days),
    date     = rep(dates, each = n_points),
    lst      = as.vector(lst_mat),
    t2m      = as.vector(t2m_mat),
    x        = rep(gvi_agg$x,        n_days),
    y        = rep(gvi_agg$y,        n_days),
    out_b    = rep(gvi_agg$out_b,    n_days),
    build_h  = rep(gvi_agg$build_h,  n_days),
    build_v  = rep(gvi_agg$build_v,  n_days),
    pop_dens = rep(gvi_agg$pop_dens, n_days),
    water    = rep(gvi_agg$water,    n_days),
    elevation= rep(gvi_agg$elevation,n_days),
    lcz      = rep(gvi_agg$lcz,      n_days)
  )

  # Free the large raster matrices and (most of) the wide covariate frame:
  # everything we still need is already inside panel_df.
  rm(lst_mat, t2m_mat); gc()

  # Derived date columns (in place via :=)
  panel_df[, year  := as.integer(format(date, "%Y"))]
  panel_df[, month := as.integer(format(date, "%m"))]
  panel_df[, ym    := format(date, "%Y-%m")]
  panel_df[, doy   := as.integer(format(date, "%j"))]

  # Drop rows with missing/infinite LST or T2M (in place: no copy)
  panel_df <- panel_df[is.finite(lst) & is.finite(t2m)]
  panel_df[, lcz := factor(lcz, levels = 1:10)]

  message("  Final panel: ", format(nrow(panel_df), big.mark=","), " observations")
  message("  Unique points: ", data.table::uniqueN(panel_df$point_id))
  message("  Unique days: ",   data.table::uniqueN(panel_df$date))

  # 7. PREPARE MODEL DATA
  message("\n[7] Preparing model data...")
  # In-place derived columns — avoids the dplyr mutate copy of the full panel
  panel_df[, log_pop_dens := log1p(pop_dens)]
  panel_df[, log_build_h  := log1p(build_h)]
  panel_df[, log_build_v  := log1p(build_v)]
  panel_df[, t2m_c        := t2m - mean(t2m, na.rm = TRUE)]

  # Free the inputs that are now redundant with panel_df. The raster stacks
  # (held both directly and inside lst_data/t2m_data) and the wide covariate
  # frame hold significant memory; dropping them here keeps peak RAM near
  # just panel_df during model fitting.
  rm(lst_stack, t2m_stack, lst_data, t2m_data,
     gvi_agg, pts_wgs, pts_proj,
     build_h, build_v, pop_dens, water, lcz_rast)
  gc()
  
  cat("\n--- Panel Summary ---\n")
  cat(sprintf("  LST (%s): mean=%.1f, sd=%.1f, range=[%.1f, %.1f]\n",
              toupper(LST_STAT), mean(panel_df$lst), sd(panel_df$lst),
              min(panel_df$lst), max(panel_df$lst)))
  cat(sprintf("  T2M (%s): mean=%.1f, sd=%.1f, range=[%.1f, %.1f]\n",
              toupper(T2M_STAT), mean(panel_df$t2m), sd(panel_df$t2m),
              min(panel_df$t2m), max(panel_df$t2m)))
  cat(sprintf("  Correlation LST-T2M: r=%.3f\n", cor(panel_df$lst, panel_df$t2m)))
  
  # 8. FIT MODEL LADDER
  message("\n[8] Fitting model ladder...")
  models <- list()
  tryCatch({ models$m0 <- feols(lst ~ t2m, data = panel_df, cluster = ~point_id)
  }, error = function(e) warning("Model 0 failed: ", e$message))
  tryCatch({ models$m1 <- feols(lst ~ t2m | ym, data = panel_df, cluster = ~point_id)
  }, error = function(e) warning("Model 1 failed: ", e$message))
  tryCatch({
    models$m2 <- feols(
      lst ~ t2m + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation | ym + lcz,
      data = panel_df, cluster = ~point_id)
  }, error = function(e) warning("Model 2 failed: ", e$message))
  tryCatch({
    models$m3 <- feols(
      lst ~ t2m_c + I(t2m_c^2) + out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
        t2m_c:out_b + t2m_c:water + t2m_c:lcz | ym + lcz,
      data = panel_df, cluster = ~point_id)
  }, error = function(e) warning("Model 3 failed: ", e$message))
  
  # 9. EVALUATION
  message("\n[9] Evaluating models...")
  pretty_names <- c(m0 = "m0_t2m_only", m1 = "m1_ymFE",
                    m2 = "m2_controls_lczFE", m3 = "m3_interactions")
  eval_results <- bind_rows(lapply(names(models), function(nm) {
    if (!is.null(models[[nm]])) eval_model(models[[nm]], panel_df, pretty_names[[nm]])
  }))
  print(eval_results)
  
  # 10. SAVE OUTPUTS
  message("\n[10] Saving outputs...")
  city_slug <- tolower(gsub(" ", "_", city_provide))
  
  write.csv(eval_results,
            file.path(OUTPUT_DIR, sprintf("%s_LST%s_T2M%s_evaluation.csv", city_slug, LST_STAT, T2M_STAT)),
            row.names = FALSE)
  
  panel_out <- if (nrow(panel_df) > 1e6) {
    message("  Saving 10 % panel sample...")
    dplyr::sample_frac(panel_df, 0.1)
  } else panel_df
  saveRDS(panel_out,
          file.path(OUTPUT_DIR, sprintf("%s_LST%s_T2M%s_panel%s.rds",
                                        city_slug, LST_STAT, T2M_STAT, if (nrow(panel_df) > 1e6) "_sample" else "")))
  
  model_summary <- list(
    city = city_provide, lst_stat = LST_STAT, t2m_stat = T2M_STAT,
    n_points = n_distinct(panel_df$point_id), n_days = n_distinct(panel_df$date),
    n_obs = nrow(panel_df), years = years, evaluation = eval_results,
    coef_m2 = if (!is.null(models$m2)) coef(models$m2) else NULL,
    coef_m3 = if (!is.null(models$m3)) coef(models$m3) else NULL)
  saveRDS(model_summary,
          file.path(OUTPUT_DIR, sprintf("%s_LST%s_T2M%s_model_summary.rds", city_slug, LST_STAT, T2M_STAT)))
  
  message("  Outputs saved to: ", OUTPUT_DIR)
  
  list(city = city_provide, lst_stat = LST_STAT, t2m_stat = T2M_STAT,
       panel = panel_df, models = models, evaluation = eval_results)
}

# MULTICITY WRAPPER

run_emulator_multicities <- function(cities, years = 2008:2017, sample_frac = 1) {
  # IMPORTANT: do NOT keep the per-city `res` (which contains the full panel
  # and fitted models) in memory across cities. For 142 cities the panels
  # accumulate into many GB. We retain only the small evaluation summaries
  # per city, write the per-city outputs to disk inside the called function,
  # and force gc() between cities.
  all_eval <- data.frame()
  all_results <- list()   # metadata-only return value (no panels, no models)

  for (city in cities) {
    message("\n\n========== Processing: ", city, " ==========\n")
    tryCatch({
      res <- estimate_climate_emulator(city, years, sample_frac)
      if (!is.null(res)) {
        res$evaluation$city <- city
        all_eval <- rbind(all_eval, res$evaluation)
        # Keep only the lightweight metadata — drop panel and models.
        all_results[[city]] <- list(
          city       = res$city,
          lst_stat   = res$lst_stat,
          t2m_stat   = res$t2m_stat,
          evaluation = res$evaluation
        )
      }
    }, error = function(e) warning("Failed for ", city, ": ", e$message))
    if (exists("res")) rm(res)
    gc()
  }

  if (nrow(all_eval) > 0)
    write.csv(all_eval,
              file.path(OUTPUT_DIR, sprintf("all_cities_LST%s_T2M%s_evaluation.csv", LST_STAT, T2M_STAT)),
              row.names = FALSE)

  all_results
}

# RUN

PROVIDE_CITIES_FILE <- "cities_provide.csv"
cities_df <- read.csv(PROVIDE_CITIES_FILE, stringsAsFactors = FALSE)

if (sys.nframe() == 0) {
  cat("\n================================================================================\n")
  cat("   CLIMATE EMULATOR: T2M -> LST Bridge\n")
  cat("   Looping over all PROVIDE cities\n")
  cat("   Metrics: LST daily ", toupper(LST_STAT), " | T2M daily ", toupper(T2M_STAT), "\n")
  cat("================================================================================\n\n")

  # Streaming loop instead of lapply(): per-city results are written to disk
  # inside estimate_climate_emulator() and then explicitly dropped, so peak
  # RAM stays at one city's panel rather than 142.
  for (XXX in cities_df$city) {
    tryCatch(
      estimate_climate_emulator(XXX, years = 2008:2017),
      error = function(e) warning("Failed for ", XXX, ": ", e$message)
    )
    gc()
  }

  cat("\n[DONE] Climate emulator script completed.\n")
  cat("  Metrics used: LST daily ", toupper(LST_STAT), " | T2M daily ", toupper(T2M_STAT), "\n")
  cat("  Outputs saved to: ", OUTPUT_DIR, "\n")
}
