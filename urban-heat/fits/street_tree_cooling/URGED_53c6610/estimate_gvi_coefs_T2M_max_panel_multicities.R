# estimate_gvi_coefs_T2M_panel_multicities.R
# T2M max with point extraction + monthly panel method
# for multiple/all PROVIDE cities

setwd("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/URGED/armande")

suppressPackageStartupMessages({
  library(terra)
  library(sf)
  library(dplyr)
  library(tidyr)
  library(fixest)
  library(ncdf4)
  library(httr)
  library(elevatr)
})

terra::gdalCache(4000)

BASE_URL <- "https://provide.marvin.vito.be/ftp/compressed_daily"
YEARS <- 2008:2017
DATA_DIR <- "H:/ECIP/Falchetta/Downloads/compressed_daily"
OUTPUT_DIR <- "outputs/elasticities"

dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

# city name mapping: PROVIDE name -> GVI name
CITY_MAPPING <- list(
  "Rome" = "Rome",
  "Milan" = "Milan",
  "Naples" = "Naples",
  "Turin" = "Turin",
  "Paris" = "Paris",
  "London" = "London",
  "Berlin" = "Berlin",
  "Madrid" = "Madrid",
  "Barcelona" = "Barcelona",
  "Vienna" = "Vienna",
  "Amsterdam" = "Amsterdam",
  "Brussels" = "Brussels",
  "Athens" = "Athens",
  "Lisbon" = "Lisbon",
  "Prague" = "Prague",
  "Budapest" = "Budapest",
  "Warsaw" = "Warsaw",
  "Copenhagen" = "Copenhagen",
  "Stockholm" = "Stockholm",
  "Helsinki" = "Helsinki",
  "Dublin" = "Dublin",
  "Oslo" = "Oslo",
  "Zurich" = "Zurich",
  "Geneva" = "Geneva",
  "Lyon" = "Lyon",
  "Marseille" = "Marseille",
  "Munich" = "Munich",
  "Hamburg" = "Hamburg",
  "Cologne" = "Cologne",
  "Frankfurt am Main" = "Frankfurt am Main",
  "Bologna" = "Bologna",
  "Palermo" = "Palermo",
  "Genoa" = "Genoa",
  "Bari" = "Bari",
  "Padua" = "Padua",
  "Ljubljana" = "Ljubljana",
  "Zagreb" = "Zagreb",
  "Bratislava" = "Bratislava",
  "Sofia" = "Sofia",
  "Bucharest" = "Bucharest",
  "Riga" = "Riga",
  "Vilnius" = "Vilnius",
  "Tallinn" = "Tallinn",
  "Krakow" = "Krakow",
  "Lagos" = "Lagos",
  "Nairobi" = "Nairobi",
  "Cairo" = "Cairo",
  "Cape Town" = "Cape Town",
  "Accra" = "Accra",
  "Dakar" = "Dakar",
  "Istanbul" = "Istanbul",
  "Dubai" = "Dubai",
  "Singapore" = "Singapore",
  "Tokyo" = "Tokyo",
  "Hong Kong" = "Hong Kong",
  "Jakarta" = "Jakarta",
  "Chennai" = "Chennai",
  "Karachi" = "Karachi",
  "Lima" = "Lima",
  "Bogota" = "Bogota",
  "Santiago" = "Santiago",
  "Buenos Aires" = "Buenos Aires",
  "Mexico City" = "Mexico City",
  "Toronto" = "Toronto",
  "Melbourne" = "Melbourne",
  "Sydney" = "Sydney",
  "Auckland" = "Auckland",
  "New York" = "New York",
  "Los Angeles" = "Los Angeles",
  "Houston" = "Houston",
  "Phoenix" = "Phoenix",
  # Mismatched names between PROVIDE and GVI data
  "Addis Abeba" = "Addis Ababa",
  "Teheran" = "Tehran",
  "Belgrado" = "Belgrade",
  "Tschwane" = "Pretoria",
  "Sevilla" = "Seville",
  "Goteborg" = "Gothenburg",
  "Ho Chi Minh" = "Ho Chi Minh City",
  "Rotterdam" = "Rotterdam [The Hague]",
  "Islamabad" = "Rawalpindi [Islamabad]",
  "Liege" = "Liège",
  "Kosice" = "Košice",
  "Cluj Napoca" = "Cluj-Napoca",
  "Gyor" = "Győr",
  "Pecs" = "Pécs",
  "Trieste" = "Triest",
  "Alicante" = "Alacant / Alicante",
  "Malaga" = "Málaga",
  "Frankfurt Am Main" = "Frankfurt am Main",
  "Palma De Mallorca" = "Palma de Mallorca"
)

download_T2M_file <- function(city_provide, year, var = "max") {
  filename <- sprintf("T2M_year_daily_%s_%d.nc", var, year)
  # provide uses underscores in folder names
  city_folder <- gsub(" ", "_", city_provide)
  local_path <- file.path(DATA_DIR, city_folder, filename)
  
  if (file.exists(local_path) && file.size(local_path) > 1000) {
    return(local_path)
  }
  
  dir.create(dirname(local_path), showWarnings = FALSE, recursive = TRUE)
  
  # provide uses underscores in URLs
  city_url <- gsub(" ", "_", city_provide)
  url <- sprintf("%s/%s/%s", BASE_URL, city_url, filename)
  
  message("  [", year, "] Downloading T2M ", var, "...")
  resp <- GET(url, write_disk(local_path, overwrite = TRUE), progress(), timeout(600))
  
  if (http_error(resp)) {
    if (file.exists(local_path)) unlink(local_path)
    stop("Failed: ", url)
  }
  
  Sys.sleep(0.5)
  return(local_path)
}

estimate_T2M_panel <- function(city_provide, T2M_var = "max", years = 2008:2017) {
  
  # getting GVI city name
  city_gvi <- CITY_MAPPING[[city_provide]]
  if (is.null(city_gvi)) {
    city_gvi <- city_provide  # Try same name
  }
  
  message("\n================================================================")
  message("  T2M PANEL - Point extraction + Monthly panel")
  message("  PROVIDE city: ", city_provide)
  message("  GVI city: ", city_gvi)
  message("  T2M var: ", T2M_var)
  message("================================================================\n")
  
  # loading GVI
  message("[1] Loading GVI data...")
  gvi_all <- readRDS("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/ugs/after_points_100425_citynames.rds")
  gvi_agg <- gvi_all %>%
    filter(city == city_gvi) %>%
    group_by(x, y) %>%
    summarise(out_b = max(out_b, na.rm = TRUE), .groups = "drop")
  
  if (nrow(gvi_agg) == 0) {
    warning("No GVI points found for city: ", city_gvi)
    return(NULL)
  }
  
  # keeping GVI in 0-100 scale (raw_gvi approach => to reflect Giacomos script)
  message("  Points: ", nrow(gvi_agg))
  message("  GVI range: ", round(min(gvi_agg$out_b), 1), " - ", round(max(gvi_agg$out_b), 1))
  
  # checking/downloading T2M
  message("\n[2] Checking T2M data...")
  city_folder <- gsub(" ", "_", city_provide)
  nc_files <- file.path(DATA_DIR, city_folder, sprintf("T2M_year_daily_%s_%d.nc", T2M_var, years))
  
  # checking which files exist
  missing_years <- years[!file.exists(nc_files)]
  if (length(missing_years) > 0) {
    message("  Missing years: ", paste(missing_years, collapse = ", "))
    message("  Attempting download...")
    tryCatch({
      nc_files <- sapply(years, function(y) download_T2M_file(city_provide, y, T2M_var))
    }, error = function(e) {
      warning("Failed to download T2M for ", city_provide, ": ", e$message)
      return(NULL)
    })
  }
  
  if (!all(file.exists(nc_files))) {
    warning("Missing T2M files for ", city_provide)
    return(NULL)
  }
  
  # looading rasters
  message("\n[3] Loading T2M rasters...")
  nc <- nc_open(nc_files[1])
  x_vals <- ncvar_get(nc, "x")
  y_vals <- ncvar_get(nc, "y")
  nc_text <- capture.output(print(nc))
  epsg_city <- as.integer(gsub("[^0-9]", "", grep("EPSG", nc_text, value = TRUE)[1]))
  nc_close(nc)
  
  dx <- abs(max(diff(x_vals)))
  dy <- abs(max(diff(y_vals)))
  xmin <- min(x_vals) - dx/2; xmax <- max(x_vals) + dx/2
  ymin <- min(y_vals) - dy/2; ymax <- max(y_vals) + dy/2
  
  T2M_list <- lapply(nc_files, function(f) {
    r <- rast(f)
    ext(r) <- c(xmin, xmax, ymin, ymax)
    crs(r) <- paste0("EPSG:", epsg_city)
    return(r)
  })
  T2M_stack <- rast(T2M_list)
  message("  Total days: ", nlyr(T2M_stack))
  
  # adding covariates
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
  
  # elevation (like in the original script)
  message("  Extracting elevation...")
  tryCatch({
    elev_pts <- get_elev_point(pts_wgs, prj = 4326, src = "aws", z = 10)
    gvi_agg$elevation <- elev_pts$elevation
    gvi_agg$elevation[is.na(gvi_agg$elevation)] <- 0
  }, error = function(e) {
    warning("Elevation extraction failed: ", e$message)
    gvi_agg$elevation <<- 0
  })
  
  gvi_agg <- gvi_agg %>% filter(lcz >= 1 & lcz <= 9)
  message("  Points after LCZ filter: ", nrow(gvi_agg))
  
  if (nrow(gvi_agg) < 100) {
    warning("Too few points after LCZ filter for ", city_gvi)
    return(NULL)
  }
  
  # extracting T2M (point extraction)
  message("\n[5] Extracting T2M at points...")
  pts_wgs <- st_as_sf(gvi_agg, coords = c("x", "y"), crs = 4326, remove = FALSE)
  pts_proj <- st_transform(pts_wgs, epsg_city)
  T2M_extract <- terra::extract(T2M_stack, vect(pts_proj))
  T2M_mat <- as.matrix(T2M_extract[, -1])
  
  # T2M should already be in Celsius, but checking for Kelvin
  if (any(T2M_mat > 200, na.rm = TRUE)) {
    message("  Converting Kelvin to Celsius...")
    T2M_mat <- T2M_mat - 273.15
  }
  message("  T2M range: ", round(min(T2M_mat, na.rm=TRUE), 1), " - ",
          round(max(T2M_mat, na.rm=TRUE), 1), " C")
  
  # creating monthly panel
  message("\n[6] Creating monthly panel...")
  dates <- seq(as.Date(paste0(min(years), "-01-01")),
               as.Date(paste0(max(years), "-12-31")), by = "day")
  dates <- dates[1:min(length(dates), ncol(T2M_mat))]
  
  ym_vec <- format(dates, "%Y-%m")
  ym_list <- split(1:length(dates), ym_vec)
  
  T2M_monthly <- sapply(ym_list, function(idx) {
    rowMeans(T2M_mat[, idx, drop = FALSE], na.rm = TRUE)
  })
  
  n_points <- nrow(gvi_agg)
  n_months <- ncol(T2M_monthly)
  ym_names <- colnames(T2M_monthly)
  
  panel_df <- data.frame(
    point_id = rep(1:n_points, n_months),
    ym = rep(ym_names, each = n_points),
    T2M = as.vector(T2M_monthly),
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
  panel_df <- panel_df %>% filter(!is.na(T2M) & !is.infinite(T2M))
  
  saveRDS(panel_df, file.path(OUTPUT_DIR, paste0(tolower(gsub(" ", "_", city_provide)),
                                                   "_T2Mmax_monthly_panel_data.rds")))
  
  message("  Panel: ", nrow(panel_df), " obs, ", length(unique(panel_df$point_id)), " points")
  
  # reg => LCZ × Month 
  message("\n[7] Running regressions (by LCZ, month interactions)...")
  
  lcz_month_results <- list()
  for (l in 1:9) {
    df_lcz <- panel_df %>% filter(lcz == l)
    if (nrow(df_lcz) < 500) next
    tryCatch({
      # model: T2M ~ out_b:factor(month) + covariates | month + year
      m <- feols(T2M ~ out_b:factor(month) + build_h + build_v + pop_dens + water + elevation | month + year,
                 data = df_lcz)
      coefs <- coef(m)[grepl("out_b:", names(coef(m)))]
      ses <- se(m)[grepl("out_b:", names(se(m)))]
      lcz_month_results[[as.character(l)]] <- list(coefs = coefs, se = ses, nobs = nobs(m))
    }, error = function(e) NULL)
  }
  
  # output
  message("\n[8] Formatting output...")
  
  output_rows <- list()
  for (lcz in names(lcz_month_results)) {
    coefs <- lcz_month_results[[lcz]]$coefs
    ses <- lcz_month_results[[lcz]]$se
    for (m in 1:12) {
      nm <- paste0("out_b:factor(month)", m)
      if (nm %in% names(coefs)) {
        output_rows[[length(output_rows) + 1]] <- data.frame(
          city = city_provide,
          lcz = as.integer(lcz),
          month = m,
          coef = coefs[nm],
          se = ses[nm],
          stringsAsFactors = FALSE
        )
      }
    }
  }
  
  if (length(output_rows) > 0) {
    result_df <- do.call(rbind, output_rows)
    rownames(result_df) <- NULL
    result_df$t_stat <- result_df$coef / result_df$se
  } else {
    result_df <- NULL
  }
  
  # saving city-specific results
  output_file <- file.path(OUTPUT_DIR, paste0(tolower(gsub(" ", "_", city_provide)),
                                              "_T2Mmax_monthly_panel.rds"))
  saveRDS(list(
    city_provide = city_provide,
    city_gvi = city_gvi,
    T2M_var = T2M_var,
    method = "point_monthly_panel",
    lcz_month_results = lcz_month_results,
    panel_summary = list(
      n_points = length(unique(panel_df$point_id)),
      n_yearmonths = length(unique(panel_df$ym)),
      n_obs = nrow(panel_df)
    )
  ), output_file)
  message("  Saved: ", output_file)
  
  return(result_df)
}

# function to run for multiple cities
run_all_cities_T2M <- function(cities, T2M_var = "max", years = 2008:2017) {
  all_results <- list()
  
  for (city in cities) {
    message("\n\n========== Processing: ", city, " ==========\n")
    tryCatch({
      res <- estimate_T2M_panel(city, T2M_var, years)
      if (!is.null(res) && nrow(res) > 0) {
        all_results[[city]] <- res
      }
    }, error = function(e) {
      warning("Failed for ", city, ": ", e$message)
    })
  }
  
  return(all_results)
}
