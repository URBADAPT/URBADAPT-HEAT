# run_LST_max_all_cities_raw_gvi.R
# Run GVI -> LST max analysis with GVI in original 0-100 scale
#

setwd("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/URGED/")

suppressPackageStartupMessages({
  library(terra)
  library(sf)
  library(dplyr)
  library(tidyr)
  library(fixest)
  library(ncdf4)
  library(elevatr)
})

terra::gdalCache(4000)

YEARS <- 2008:2017
DATA_DIR <- "H:/ECIP/Falchetta/Downloads/compressed_daily"
OUTPUT_DIR <- "outputs/elasticities"

# Source the main script to get helper functions and city mapping
source("estimate_gvi_coefs_LST_panel_multicities.R")

# Override the estimate function to keep GVI in 0-100 scale
estimate_lst_panel_raw_gvi <- function(city_provide, lst_var = "max", years = 2008:2017) {

  city_gvi <- CITY_MAPPING[[city_provide]]
  if (is.null(city_gvi)) city_gvi <- city_provide

  message("\n================================================================")
  message("  LST PANEL - Raw GVI scale (0-100)")
  message("  PROVIDE city: ", city_provide)
  message("  GVI city: ", city_gvi)
  message("  LST var: ", lst_var)
  message("================================================================\n")

  # 1. Load GVI - DO NOT divide by 100
  message("[1] Loading GVI data (keeping 0-100 scale)...")
  gvi_all <- readRDS("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/ugs/after_points_100425_citynames.rds")
  gvi_agg <- gvi_all %>%
    filter(city == city_gvi) %>%
    group_by(x, y) %>%
    summarise(out_b = mean(out_b, na.rm = TRUE), .groups = "drop")

  if (nrow(gvi_agg) == 0) {
    warning("No GVI points for: ", city_gvi)
    return(NULL)
  }

  # Keep GVI in 0-100 scale 
  # gvi_agg$out_b <- gvi_agg$out_b / 100  # COMMENTED OUT
  message("  Points: ", nrow(gvi_agg))
  message("  GVI range: ", round(min(gvi_agg$out_b), 1), " - ", round(max(gvi_agg$out_b), 1))

  # 2. Load LST files
  message("\n[2] Loading LST data...")
  nc_files <- file.path(DATA_DIR, city_provide, sprintf("LST_year_daily_%s_%d.nc", lst_var, years))
  if (!all(file.exists(nc_files))) {
    warning("Missing LST files for ", city_provide)
    return(NULL)
  }

  # 3. Load rasters
  message("\n[3] Loading LST rasters...")
  nc <- nc_open(nc_files[1])
  x_vals <- ncvar_get(nc, "x")
  y_vals <- ncvar_get(nc, "y")
  nc_text <- capture.output(print(nc))
  epsg_city <- as.integer(gsub("[^0-9]", "", grep("EPSG", nc_text, value = TRUE)[1]))
  nc_close(nc)

  dx <- abs(mean(diff(x_vals)))
  dy <- abs(mean(diff(y_vals)))
  xmin <- min(x_vals) - dx/2; xmax <- max(x_vals) + dx/2
  ymin <- min(y_vals) - dy/2; ymax <- max(y_vals) + dy/2

  lst_list <- lapply(nc_files, function(f) {
    r <- rast(f)
    ext(r) <- c(xmin, xmax, ymin, ymax)
    crs(r) <- paste0("EPSG:", epsg_city)
    return(r)
  })
  lst_stack <- rast(lst_list)
  message("  Total days: ", nlyr(lst_stack))

  # 4. Add covariates
  message("\n[4] Adding covariates...")
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)

  lcz_rast <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/climate/lcz/lcz_filter_v3.tif")
  gvi_agg$lcz <- terra::extract(lcz_rast, vect(pts_wgs))[[2]]

  build_h <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif")
  build_v <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_BUILT_V_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  pop_dens <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/socioecon/GHS_POP_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif")
  water <- rast("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/old/c_gls_WB300_202306010000_GLOBE_S2_V2.0.1.nc", lyrs = "WB")

  gvi_agg$build_h <- terra::extract(build_h, vect(pts_wgs))[, 2]
  gvi_agg$build_v <- terra::extract(build_v, vect(pts_wgs))[, 2]
  gvi_agg$pop_dens <- terra::extract(pop_dens, vect(pts_wgs))[, 2]
  gvi_agg$water <- terra::extract(water, vect(pts_wgs))[, 2]

  gvi_agg$build_h[is.na(gvi_agg$build_h)] <- 0
  gvi_agg$build_v[is.na(gvi_agg$build_v)] <- 0
  gvi_agg$pop_dens[is.na(gvi_agg$pop_dens)] <- 0
  gvi_agg$water[is.na(gvi_agg$water)] <- 0

  message("  Extracting elevation...")
  tryCatch({
    elev_pts <- get_elev_point(pts_wgs, prj = 4326, src = "aws", z = 10)
    gvi_agg$elevation <- elev_pts$elevation
    gvi_agg$elevation[is.na(gvi_agg$elevation)] <- 0
  }, error = function(e) {
    warning("Elevation failed: ", e$message)
    gvi_agg$elevation <<- 0
  })

  gvi_agg <- gvi_agg %>% filter(lcz >= 1 & lcz <= 9)
  message("  Points after LCZ filter: ", nrow(gvi_agg))

  if (nrow(gvi_agg) < 100) {
    warning("Too few points for ", city_gvi)
    return(NULL)
  }

  # 5. Extract LST
  message("\n[5] Extracting LST at points...")
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)
  pts_proj <- st_transform(pts_wgs, epsg_city)
  lst_extract <- terra::extract(lst_stack, vect(pts_proj))
  lst_mat <- as.matrix(lst_extract[, -1])

  if (any(lst_mat > 200, na.rm = TRUE)) {
    message("  Converting Kelvin to Celsius...")
    lst_mat <- lst_mat - 273.15
  }
  message("  LST range: ", round(min(lst_mat, na.rm=TRUE), 1), " - ",
          round(max(lst_mat, na.rm=TRUE), 1), " C")

  # 6. Create monthly panel
  message("\n[6] Creating monthly panel...")
  dates <- seq(as.Date(paste0(min(years), "-01-01")),
               as.Date(paste0(max(years), "-12-31")), by = "day")
  dates <- dates[1:min(length(dates), ncol(lst_mat))]

  ym_vec <- format(dates, "%Y-%m")
  ym_list <- split(1:length(dates), ym_vec)

  lst_monthly <- sapply(ym_list, function(idx) {
    rowMeans(lst_mat[, idx, drop = FALSE], na.rm = TRUE)
  })

  n_points <- nrow(gvi_agg)
  n_months <- ncol(lst_monthly)
  ym_names <- colnames(lst_monthly)

  panel_df <- data.frame(
    point_id = rep(1:n_points, n_months),
    ym = rep(ym_names, each = n_points),
    lst = as.vector(lst_monthly),
    x = rep(gvi_agg$x, n_months),
    y = rep(gvi_agg$y, n_months),
    out_b = rep(gvi_agg$out_b, n_months),
    build_h = rep(gvi_agg$build_h, n_months),
    build_v = rep(gvi_agg$build_v, n_months),
    pop_dens = rep(gvi_agg$pop_dens, n_months),
    water = rep(gvi_agg$water, n_months),
    elevation = rep(gvi_agg$elevation, n_months),
    lcz = rep(gvi_agg$lcz, n_months)
  )

  panel_df$year <- as.integer(substr(panel_df$ym, 1, 4))
  panel_df$month <- as.integer(substr(panel_df$ym, 6, 7))
  panel_df$lcz <- factor(panel_df$lcz, levels = 1:9)
  panel_df <- panel_df %>% filter(!is.na(lst) & !is.infinite(lst))

  message("  Panel: ", nrow(panel_df), " obs, ", length(unique(panel_df$point_id)), " points")

  # 7. Regressions - LCZ × Month 
  message("\n[7] Running regressions (by LCZ, month interactions)...")

  lcz_month_results <- list()
  for (l in 1:9) {
    df_lcz <- panel_df %>% filter(lcz == l)
    if (nrow(df_lcz) < 500) next
    tryCatch({
      # value ~ out_b:as.factor(month) + covariates | month + year
      m <- feols(lst ~ out_b:factor(month) + build_h + build_v + pop_dens + water + elevation | month + year,
                 data = df_lcz)
      coefs <- coef(m)[grepl("out_b:", names(coef(m)))]
      ses <- se(m)[grepl("out_b:", names(se(m)))]
      lcz_month_results[[as.character(l)]] <- list(coefs = coefs, se = ses, nobs = nobs(m))
    }, error = function(e) NULL)
  }

  message("\n[8] Formatting output...")

  output_rows <- list()
  for (lcz in names(lcz_month_results)) {
    coefs <- lcz_month_results[[lcz]]$coefs
    ses <- lcz_month_results[[lcz]]$se
    for (m in 1:12) {
      nm <- paste0("out_b:factor(month)", m)
      if (nm %in% names(coefs)) {
        coef_value <- unname(coefs[nm])
        se_value <- unname(ses[nm])
        t_value <- coef_value / se_value
        output_rows[[length(output_rows) + 1]] <- data.frame(
          city = city_provide,
          lcz = as.integer(lcz),
          month = m,
          month_name = month.abb[m],
          coef = coef_value,
          se = se_value,
          t_stat = t_value,
          significant_05 = abs(t_value) >= 1.96,
          stringsAsFactors = FALSE
        )
      }
    }
  }

  if (length(output_rows) > 0) {
    result_df <- do.call(rbind, output_rows)
    rownames(result_df) <- NULL
  } else {
    result_df <- NULL
  }

  return(result_df)
}

# ==============================================================================
# MAIN
# ==============================================================================

# Get all cities with complete LST max data.
city_dirs <- list.dirs(DATA_DIR, recursive = FALSE, full.names = FALSE)
cities_with_max <- c()
for (city in city_dirs) {
  expected <- file.path(DATA_DIR, city, sprintf("LST_year_daily_max_%d.nc", YEARS))
  if (all(file.exists(expected)) && all(file.size(expected) > 1000)) {
    cities_with_max <- c(cities_with_max, city)
  }
}

cat("\n========================================\n")
cat("LST MAX ANALYSIS - RAW GVI SCALE (0-100)\n")
cat("========================================\n")
cat("Cities with LST max data:", length(cities_with_max), "\n")
cat("Coefficient interpretation: °C per 1 unit GVI (0-100 scale)\n\n")

# Run analysis
all_results <- list()
failed_cities <- character()
for (city in cities_with_max) {
  message("\n========== Processing: ", city, " ==========")
  tryCatch({
    res <- estimate_lst_panel_raw_gvi(city, "max", 2008:2017)
    if (!is.null(res) && nrow(res) > 0) {
      all_results[[city]] <- res
    }
  }, error = function(e) {
    warning("Failed for ", city, ": ", e$message)
    failed_cities <<- c(failed_cities, city)
  })
}

# Combine all results
cat("\n\nCombining results...\n")
if (length(all_results) == 0) {
  stop("Every requested city failed; no coefficient table can be written.")
}
combined_df <- do.call(rbind, all_results)
rownames(combined_df) <- NULL
combined_df <- combined_df %>% arrange(city, lcz, month)

key_cols <- c("city", "lcz", "month")
if (anyDuplicated(combined_df[key_cols])) {
  stop("Duplicate city-LCZ-month keys in the reconstructed coefficient table.")
}

# All exports are derived from this single canonical table.
output_with_se <- combined_df %>%
  select(city, lcz, month, month_name, coef, se, t_stat, significant_05)

output_simple <- output_with_se %>%
  select(city, lcz, month, coef)

output_significant <- output_with_se %>%
  filter(significant_05) %>%
  select(city, lcz, month, month_name, coef, se, t_stat)

output_significant_cooling <- output_significant %>%
  filter(coef < 0)

# Save without overwriting the historical inputs in the project root.
output_paths <- c(
  with_se = file.path(OUTPUT_DIR, "summary_city_lcz_month_LSTmax_raw_gvi_with_se.csv"),
  simple = file.path(OUTPUT_DIR, "summary_city_lcz_month_LSTmax_raw_gvi.csv"),
  significant = file.path(OUTPUT_DIR, "summary_city_lcz_month_LSTmax_raw_gvi_significant.csv"),
  significant_cooling = file.path(OUTPUT_DIR, "summary_city_lcz_month_LSTmax_raw_gvi_significant_cooling.csv"),
  notebook_07 = file.path(OUTPUT_DIR, "coefs_GVI_lst.csv")
)

write.csv(output_with_se, output_paths[["with_se"]], row.names = FALSE)
write.csv(output_simple, output_paths[["simple"]], row.names = FALSE)
write.csv(output_significant, output_paths[["significant"]], row.names = FALSE)
write.csv(output_significant_cooling, output_paths[["significant_cooling"]], row.names = FALSE)
write.csv(output_significant, output_paths[["notebook_07"]], row.names = FALSE)

# Verify that the two requested significant outputs are byte-identical.
alias_identical <- identical(
  tools::md5sum(output_paths[["significant"]]),
  setNames(tools::md5sum(output_paths[["notebook_07"]]), names(tools::md5sum(output_paths[["significant"]])))
)
if (!alias_identical) {
  stop("The significant export and coefs_GVI_lst.csv are not byte-identical.")
}

manifest <- data.frame(
  output = names(output_paths),
  path = unname(output_paths),
  rows = c(
    nrow(output_with_se),
    nrow(output_simple),
    nrow(output_significant),
    nrow(output_significant_cooling),
    nrow(output_significant)
  ),
  md5 = unname(tools::md5sum(output_paths)),
  stringsAsFactors = FALSE
)
write.csv(manifest, file.path(OUTPUT_DIR, "output_manifest.csv"), row.names = FALSE)

cat("\n========================================\n")
cat("DONE - RAW GVI SCALE ANALYSIS COMPLETE\n")
cat("========================================\n")
print(manifest, row.names = FALSE)
cat("Total coefficient rows:", nrow(output_with_se), "\n")
cat("Significant coefficient rows:", nrow(output_significant), "\n")
cat("Significant cooling rows:", nrow(output_significant_cooling), "\n")
cat("Cities:", length(unique(combined_df$city)), "\n")
cat("Failed cities:", if (length(failed_cities)) paste(failed_cities, collapse = ", ") else "none", "\n")
cat("\nInterpretation: 'coef' = °C change per 1 unit increase in GVI (0-100 scale)\n")
cat("This matches format.\n")
