# estimate_gvi_coefs_LST_panel_multicities.R
# LST with POINT EXTRACTION + MONTHLY PANEL method
# Generalised for multiple PROVIDE cities

setwd("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/URGED/")

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

dir.create(DATA_DIR, showWarnings = FALSE, recursive = TRUE)
dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

# City name mapping: PROVIDE name -> GVI name
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
  # Mismatched names
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
  # Title case variants for multi-word cities
  "Frankfurt Am Main" = "Frankfurt am Main",
  "Palma De Mallorca" = "Palma de Mallorca"
)

download_lst_file <- function(city_provide, year, var = "max") {
  filename <- sprintf("LST_year_daily_%s_%d.nc", var, year)
  local_path <- file.path(DATA_DIR, city_provide, filename)
  if (file.exists(local_path) && file.size(local_path) > 1000) {
    return(local_path)
  }
  dir.create(dirname(local_path), showWarnings = FALSE, recursive = TRUE)
  # PROVIDE uses underscores in URLs, not spaces
  city_url <- gsub(" ", "_", city_provide)
  url <- sprintf("%s/%s/%s", BASE_URL, city_url, filename)
  message("  [", year, "] Downloading ", var, "...")
  resp <- GET(url, write_disk(local_path, overwrite = TRUE), progress(), timeout(600))
  if (http_error(resp)) {
    if (file.exists(local_path)) unlink(local_path)
    stop("Failed: ", url)
  }
  Sys.sleep(0.5)
  return(local_path)
}

estimate_lst_panel <- function(city_provide, lst_var = "max", years = 2008:2017) {

  # Get GVI city name
  city_gvi <- CITY_MAPPING[[city_provide]]
  if (is.null(city_gvi)) {
    city_gvi <- city_provide  # Try same name
  }

  message("\n================================================================")
  message("  LST PANEL - Point extraction + Monthly panel")
  message("  PROVIDE city: ", city_provide)
  message("  GVI city: ", city_gvi)
  message("  LST var: ", lst_var)
  message("================================================================\n")

  # 1. Load GVI
  message("[1] Loading GVI data...")
  gvi_all <- readRDS("C:/Users/falchetta/OneDrive - IIASA/IBGF_2024/implementation/ugs/after_points_100425_citynames.rds")
  gvi_agg <- gvi_all %>%
    filter(city == city_gvi) %>%
    group_by(x, y) %>%
    summarise(out_b = mean(out_b, na.rm = TRUE), .groups = "drop")

  if (nrow(gvi_agg) == 0) {
    warning("No GVI points found for city: ", city_gvi)
    return(NULL)
  }

  gvi_agg$out_b <- gvi_agg$out_b / 100
  message("  Points: ", nrow(gvi_agg))

  # 2. Download LST
  message("\n[2] Downloading LST data...")
  nc_files <- tryCatch({
    sapply(years, function(y) download_lst_file(city_provide, y, lst_var))
  }, error = function(e) {
    warning("Failed to download LST for ", city_provide, ": ", e$message)
    return(NULL)
  })

  if (is.null(nc_files)) return(NULL)

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

  # Elevation (from AWS terrain tiles)
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

  # 5. Extract LST (point extraction)
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

  # 7. Regressions
  message("\n[7] Running regressions...")
  results <- list()

  # Summer filter
  panel_summer <- panel_df %>% filter(month %in% 6:8)

  # Pooled - YM + LCZ FE
  cat("\n--- Summer, YM + LCZ FE ---\n")
  tryCatch({
    m <- feols(lst ~ out_b + build_h + build_v + pop_dens + water + elevation | ym + lcz,
               data = panel_summer, cluster = ~point_id)
    print(summary(m))
    results$summer_ymLCZ <- list(coef = coef(m)["out_b"], se = se(m)["out_b"], nobs = nobs(m))
    cat("  Effect per +0.1 GVI: ", round(coef(m)["out_b"] * 0.1, 3), " C\n")
  }, error = function(e) warning(e$message))

  # By LCZ (summer)
  cat("\n--- By LCZ (summer, YM FE) ---\n")
  cat("LCZ | Coef | SE | t-val | Effect/+0.1 GVI\n")
  cat("----|------|-----|-------|----------------\n")
  lcz_results <- list()
  for (l in 1:9) {
    df <- panel_summer %>% filter(lcz == l)
    if (nrow(df) < 100) next
    tryCatch({
      m <- feols(lst ~ out_b + build_h + build_v + pop_dens + water + elevation | ym,
                 data = df, cluster = ~point_id)
      coef_val <- coef(m)["out_b"]
      se_val <- se(m)["out_b"]
      cat(sprintf(" %d  | %+.3f | %.3f | %+.1f | %+.3f C\n",
                  l, coef_val, se_val, coef_val/se_val, coef_val * 0.1))
      lcz_results[[as.character(l)]] <- list(coef = coef_val, se = se_val, nobs = nobs(m))
    }, error = function(e) NULL)
  }
  results$by_lcz <- lcz_results

  # Monthly interactions (all year)
  cat("\n--- Monthly GVI interactions (LCZ FE) ---\n")
  tryCatch({
    m_monthly <- feols(lst ~ out_b:factor(month) + build_h + build_v + pop_dens + water + elevation | ym + lcz,
                       data = panel_df, cluster = ~point_id)
    month_coefs <- coef(m_monthly)[grepl("out_b:", names(coef(m_monthly)))]
    month_ses <- se(m_monthly)[grepl("out_b:", names(se(m_monthly)))]
    results$monthly_interactions <- list(coefs = month_coefs, se = month_ses, nobs = nobs(m_monthly))

    cat("Month | Effect/+0.1 GVI | SE\n")
    cat("------|-----------------|-----\n")
    month_names <- c("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")
    for (i in 1:12) {
      nm <- paste0("out_b:factor(month)", i)
      if (nm %in% names(month_coefs)) {
        cat(sprintf("%-5s | %+14.3f°C | %.3f\n", month_names[i], month_coefs[nm] * 0.1, month_ses[nm] * 0.1))
      }
    }
  }, error = function(e) warning("Monthly interactions failed: ", e$message))

  # LCZ × Month coefficients (full year)
  cat("\n--- LCZ × Month coefficients ---\n")
  lcz_month_results <- list()
  for (l in 1:9) {
    df_lcz <- panel_df %>% filter(lcz == l)
    if (nrow(df_lcz) < 500) next
    tryCatch({
      m <- feols(lst ~ out_b:factor(month) + build_h + build_v + pop_dens + water + elevation | ym,
                 data = df_lcz, cluster = ~point_id)
      coefs <- coef(m)[grepl("out_b:", names(coef(m)))]
      ses <- se(m)[grepl("out_b:", names(se(m)))]
      lcz_month_results[[as.character(l)]] <- list(coefs = coefs, se = ses, nobs = nobs(m))
    }, error = function(e) NULL)
  }
  results$lcz_month <- lcz_month_results

  # 8. Save
  message("\n[8] Saving...")
  output <- list(
    city_provide = city_provide,
    city_gvi = city_gvi,
    lst_var = lst_var,
    method = "point_monthly_panel",
    results = results,
    panel_summary = list(
      n_points = length(unique(panel_df$point_id)),
      n_yearmonths = length(unique(panel_df$ym)),
      n_obs = nrow(panel_df)
    )
  )

  output_file <- file.path(OUTPUT_DIR, paste0(tolower(gsub(" ", "_", city_provide)),
                                               "_LST", lst_var, "_monthly_panel.rds"))
  saveRDS(output, output_file)
  message("  Saved: ", output_file)

  return(output)
}

# Function to run for multiple cities
run_all_cities <- function(cities, lst_var = "max", years = 2008:2017) {
  all_results <- list()

  for (city in cities) {
    message("\n\n========== Processing: ", city, " ==========\n")
    tryCatch({
      res <- estimate_lst_panel(city, lst_var, years)
      if (!is.null(res)) {
        all_results[[city]] <- res
      }
    }, error = function(e) {
      warning("Failed for ", city, ": ", e$message)
    })
  }

  # Save combined results
  saveRDS(all_results, file.path(OUTPUT_DIR, paste0("all_cities_LST", lst_var, "_panel.rds")))
  message("\n\nSaved combined results to: all_cities_LST", lst_var, "_panel.rds")

  # Generate summary tables
  message("\nGenerating summary tables...")
  generate_summary_tables(all_results, lst_var)

  return(all_results)
}

# Function to generate summary tables by city, month, and LCZ
generate_summary_tables <- function(all_results, lst_var = "max") {

  month_names <- c("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")

  # 1. Table: City × LCZ (summer average)
  lcz_table <- data.frame()
  for (city in names(all_results)) {
    res <- all_results[[city]]
    if (!is.null(res$results$by_lcz)) {
      for (lcz in names(res$results$by_lcz)) {
        lcz_table <- rbind(lcz_table, data.frame(
          city = city,
          lcz = as.integer(lcz),
          coef = res$results$by_lcz[[lcz]]$coef,
          se = res$results$by_lcz[[lcz]]$se,
          nobs = res$results$by_lcz[[lcz]]$nobs,
          effect_per_0.1_GVI = res$results$by_lcz[[lcz]]$coef * 0.1
        ))
      }
    }
  }

  # 2. Table: City × Month (pooled across LCZ)
  month_table <- data.frame()
  for (city in names(all_results)) {
    res <- all_results[[city]]
    if (!is.null(res$results$monthly_interactions)) {
      coefs <- res$results$monthly_interactions$coefs
      ses <- res$results$monthly_interactions$se
      for (m in 1:12) {
        nm <- paste0("out_b:factor(month)", m)
        if (nm %in% names(coefs)) {
          month_table <- rbind(month_table, data.frame(
            city = city,
            month = m,
            month_name = month_names[m],
            coef = coefs[nm],
            se = ses[nm],
            effect_per_0.1_GVI = coefs[nm] * 0.1
          ))
        }
      }
    }
  }

  # 3. Table: City × LCZ × Month
  lcz_month_table <- data.frame()
  for (city in names(all_results)) {
    res <- all_results[[city]]
    if (!is.null(res$results$lcz_month)) {
      for (lcz in names(res$results$lcz_month)) {
        coefs <- res$results$lcz_month[[lcz]]$coefs
        ses <- res$results$lcz_month[[lcz]]$se
        for (m in 1:12) {
          nm <- paste0("out_b:factor(month)", m)
          if (nm %in% names(coefs)) {
            lcz_month_table <- rbind(lcz_month_table, data.frame(
              city = city,
              lcz = as.integer(lcz),
              month = m,
              month_name = month_names[m],
              coef = coefs[nm],
              se = ses[nm],
              effect_per_0.1_GVI = coefs[nm] * 0.1
            ))
          }
        }
      }
    }
  }

  # Save tables
  write.csv(lcz_table, file.path(OUTPUT_DIR, paste0("summary_city_lcz_LST", lst_var, ".csv")), row.names = FALSE)
  write.csv(month_table, file.path(OUTPUT_DIR, paste0("summary_city_month_LST", lst_var, ".csv")), row.names = FALSE)
  write.csv(lcz_month_table, file.path(OUTPUT_DIR, paste0("summary_city_lcz_month_LST", lst_var, ".csv")), row.names = FALSE)

  message("  Saved: summary_city_lcz_LST", lst_var, ".csv")
  message("  Saved: summary_city_month_LST", lst_var, ".csv")
  message("  Saved: summary_city_lcz_month_LST", lst_var, ".csv")

  # Print summary stats
  cat("\n================================================================================\n")
  cat("                         SUMMARY STATISTICS\n")
  cat("================================================================================\n\n")

  cat("Cities processed:", length(all_results), "\n")
  cat("City × LCZ coefficients:", nrow(lcz_table), "\n")
  cat("City × Month coefficients:", nrow(month_table), "\n")
  cat("City × LCZ × Month coefficients:", nrow(lcz_month_table), "\n")

  if (nrow(lcz_table) > 0) {
    cat("\n--- Summer cooling by LCZ (median across cities) ---\n")
    lcz_summary <- lcz_table %>%
      group_by(lcz) %>%
      summarise(
        n_cities = n(),
        median_effect = median(effect_per_0.1_GVI, na.rm = TRUE),
        mean_effect = mean(effect_per_0.1_GVI, na.rm = TRUE),
        .groups = "drop"
      )
    print(lcz_summary)
  }

  return(list(lcz = lcz_table, month = month_table, lcz_month = lcz_month_table))
}

# ==============================================================================
# RUN
# ==============================================================================

# European cities with good coverage (start with these)
CITIES_EUROPE <- c(
  "Rome", "Milan", "Paris", "London", "Berlin", "Madrid", "Barcelona",
  "Vienna", "Amsterdam", "Brussels", "Athens", "Lisbon", "Prague",
  "Budapest", "Warsaw", "Copenhagen", "Stockholm", "Helsinki"
)

# Run single city (test)
# estimate_lst_panel("Rome", "max", 2008:2017)

# Run multiple cities
# run_all_cities(CITIES_EUROPE, "max", 2008:2017)
