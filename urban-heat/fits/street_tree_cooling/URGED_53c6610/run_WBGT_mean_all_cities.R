# run_WBGT_mean_all_cities.R
# run GVI -> WBGT mean analysis for all PROVIDE cities
# creates outputs:
#   - summary_city_lcz_month_WBGTmean_raw_gvi.csv
#   - summary_city_lcz_month_WBGTmean_raw_gvi_significant.csv
#   - summary_city_lcz_month_WBGTmean_raw_gvi_with_se.csv


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

YEARS <- 2008:2017
DATA_DIR <- "H:/ECIP/Falchetta/Downloads/compressed_daily"
OUTPUT_DIR <- "outputs/elasticities"

dir.create(OUTPUT_DIR, showWarnings = FALSE, recursive = TRUE)

# estimation script
source("estimate_gvi_coefs_WBGT_mean_panel_multicities.R")

# MAIN

cat("\n============================================================\n")
cat("WBGT mean ANALYSIS - RAW GVI SCALE (0-100)\n")
cat("============================================================\n")
cat("Coefficient interpretation: °C per 1 unit GVI (0-100 scale)\n\n")

# getting list of cities with WBGT data
# checking which cities have complete WBGT mean data (10 years)
city_dirs <- list.dirs(DATA_DIR, recursive = FALSE, full.names = FALSE)
cities_with_wbgt <- c()

for (city in city_dirs) {
  files <- list.files(file.path(DATA_DIR, city), pattern = "WBGT_year_daily_mean_20")
  if (length(files) >= 10) {
    # converting folder name back to city name (underscores -> spaces for some)
    city_name <- gsub("_", " ", city)
    cities_with_wbgt <- c(cities_with_wbgt, city_name)
  }
}

cat("Cities with complete WBGT mean data (10 years):", length(cities_with_wbgt), "\n")

if (length(cities_with_wbgt) == 0) {
  cat("\nNo cities with complete WBGT data found.\n")
  cat("Run download_WBGT_mean_all_cities.R first to download the data.\n")
  stop("No WBGT data available")
}

cat("Cities to process:\n")
for (i in seq_along(cities_with_wbgt)) {
  cat(sprintf("  [%d] %s\n", i, cities_with_wbgt[i]))
}

# running analysis for all cities
cat("\n--- Starting estimation ---\n")
all_results <- list()
failed_cities <- c()

for (i in seq_along(cities_with_wbgt)) {
  city <- cities_with_wbgt[i]
  cat(sprintf("\n========== [%d/%d] %s ==========\n", i, length(cities_with_wbgt), city))
  
  tryCatch({
    res <- estimate_wbgt_panel(city, "mean", YEARS)
    if (!is.null(res) && nrow(res) > 0) {
      all_results[[city]] <- res
      cat("  SUCCESS:", nrow(res), "coefficients\n")
    } else {
      cat("  SKIPPED: No valid results\n")
      failed_cities <- c(failed_cities, city)
    }
  }, error = function(e) {
    cat("  FAILED:", e$message, "\n")
    failed_cities <- c(failed_cities, city)
  })
}

# COMBINE AND SAVE RESULTS

cat("\n\n============================================================\n")
cat("COMBINING RESULTS\n")
cat("============================================================\n")

if (length(all_results) == 0) {
  stop("No results to save!")
}

# combine all results
combined_df <- do.call(rbind, all_results)
rownames(combined_df) <- NULL

# adding month names
month_names <- c("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")
combined_df$month_name <- month_names[combined_df$month]

# calculating/adding significance
combined_df$significant_05 <- abs(combined_df$t_stat) >= 1.96

cat("Total coefficients:", nrow(combined_df), "\n")
cat("Cities:", length(unique(combined_df$city)), "\n")
cat("Significant (p<0.05):", sum(combined_df$significant_05), "(",
    round(100*mean(combined_df$significant_05), 1), "%)\n")

# OUTPUT FILES 

cat("\n--- Saving output files ---\n")

# full results with SE
output_with_se <- combined_df %>%
  select(city, lcz, month, month_name, coef, se, t_stat, significant_05)
write.csv(output_with_se,
          file.path(OUTPUT_DIR, "summary_city_lcz_month_WBGTmean_raw_gvi_with_se.csv"),
          row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi_with_se.csv\n")

# simple format (raw gvi)
output_simple <- combined_df %>%
  select(city, lcz, month, coef)
write.csv(output_simple,
          file.path(OUTPUT_DIR, "summary_city_lcz_month_WBGTmean_raw_gvi.csv"),
          row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi.csv\n")

# saving also to root directory 
write.csv(output_simple, "summary_city_lcz_month_WBGTmean_raw_gvi.csv", row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi.csv (root)\n")

# signi only
output_significant <- combined_df %>%
  filter(significant_05) %>%
  select(city, lcz, month, month_name, coef, se, t_stat)
write.csv(output_significant,
          file.path(OUTPUT_DIR, "summary_city_lcz_month_WBGTmean_raw_gvi_significant.csv"),
          row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi_significant.csv\n")

# save to root directory
write.csv(output_significant, "summary_city_lcz_month_WBGTmean_raw_gvi_significant.csv", row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi_significant.csv (root)\n")

# significant cooling only (no warming coeff)
output_sig_cooling <- combined_df %>%
  filter(significant_05 & coef < 0) %>%
  select(city, lcz, month, month_name, coef, se, t_stat)
write.csv(output_sig_cooling,
          file.path(OUTPUT_DIR, "summary_city_lcz_month_WBGTmean_raw_gvi_significant_cooling.csv"),
          row.names = FALSE)
cat("  Saved: summary_city_lcz_month_WBGTmean_raw_gvi_significant_cooling.csv\n")

# SUMMARY STATISTICS

cat("\n============================================================\n")
cat("SUMMARY STATISTICS\n")
cat("============================================================\n\n")

cat("--- Overall ---\n")
cat(sprintf("Total coefficients: %d\n", nrow(combined_df)))
cat(sprintf("Mean coefficient: %.5f\n", mean(combined_df$coef)))
cat(sprintf("Median coefficient: %.5f\n", median(combined_df$coef)))
cat(sprintf("Significant (p<0.05): %d (%.1f%%)\n",
            sum(combined_df$significant_05), 100*mean(combined_df$significant_05)))
cat(sprintf("Cooling (negative): %d (%.1f%%)\n",
            sum(combined_df$coef < 0), 100*mean(combined_df$coef < 0)))

cat("\n--- By LCZ ---\n")
lcz_summary <- combined_df %>%
  group_by(lcz) %>%
  summarise(
    n = n(),
    mean_coef = mean(coef),
    pct_cooling = 100 * mean(coef < 0),
    pct_significant = 100 * mean(significant_05),
    .groups = "drop"
  )
print(lcz_summary)

cat("\n--- By Month ---\n")
month_summary <- combined_df %>%
  group_by(month, month_name) %>%
  summarise(
    n = n(),
    mean_coef = mean(coef),
    pct_cooling = 100 * mean(coef < 0),
    pct_significant = 100 * mean(significant_05),
    .groups = "drop"
  )
print(month_summary)

if (length(failed_cities) > 0) {
  cat("\n--- Failed cities ---\n")
  cat(paste(failed_cities, collapse = ", "), "\n")
}

cat("\n============================================================\n")
cat("DONE - WBGT mean ANALYSIS COMPLETE\n")
cat("============================================================\n")
cat("Interpretation: 'coef' = °C WBGT change per 1 unit increase in GVI (0-100 scale)\n")
