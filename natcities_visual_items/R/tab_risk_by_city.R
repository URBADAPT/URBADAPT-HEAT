# =============================================================================
# tab_risk_by_city.R  --  big summary table: hazard / exposure / risk by city
# Columns: City, Country, Cluster, Pop (k), Warm-season T2M (degC), Hot days,
#          Baseline heat deaths/yr (2020), Heat deaths / 100k. CSV + LaTeX.
# Pulls climate metrics from city_metadata.csv and baseline mortality from
# the per-city results.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_tab_risk <- function(cities = discover_cities(require_tables = FALSE)) {
  banner("Table: hazard / exposure / risk by city")
  meta <- load_city_meta()
  if (is.null(meta)) { message("No metadata."); return(invisible(NULL)) }

  mort <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", c),
                       quiet = TRUE)
    if (is.null(d)) return(NULL)
    data.frame(deaths_2020 = d$deaths_overall[d$year == min(d$year)][1])
  })

  df <- meta
  if (!is.null(mort)) df <- dplyr::left_join(df, mort[, c("city", "deaths_2020")], by = "city")
  else df$deaths_2020 <- NA_real_
  df$mort_100k <- per_100k(df$deaths_2020, df$pop_k)
  df <- df[order(df$climate_cluster, -df$warmseason_mean_t2m), ]

  out <- data.frame(
    City                 = df$city_label,
    Country              = df$country,
    Cluster              = as.character(df$climate_cluster),
    `Pop (k)`            = round(df$pop_k, 0),
    `Warm-season T2M (°C)` = round(df$warmseason_mean_t2m, 1),
    `Hot days`           = df$hot_days,
    `Heat deaths/yr (2020)` = round(df$deaths_2020, 0),
    `Heat deaths / 100k`    = round(df$mort_100k, 1),
    check.names = FALSE)

  save_table(out, "tab_risk_by_city",
             caption = "Heat hazard, exposure, and baseline risk by city and climate cluster.",
             label = "tab:risk_by_city",
             longtable = TRUE, size = "\\footnotesize",
             align = c("l", "l", "l", "l", "r", "r", "r", "r", "r"),
             digits = c(0, 0, 0, 0, 0, 1, 0, 0, 1))
  print(out, row.names = FALSE)
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_risk()
