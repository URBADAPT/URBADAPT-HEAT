# reconstruct_rr_curves.R
# Reconstruct city- and age-specific RR(T) curves from Masselot et al. (2023)
# Zenodo data (https://zenodo.org/records/10288665).
#
# Masselot reports relative-risk curves centred on the city/age-specific MMT.
# For CLIMADA, these curves must be converted to daily per-person heat
# mortality probabilities:
#
#   AF_heat(T) = max(0, (RR(T) - 1) / RR(T))  for T >= MMT
#   mdd(T)     = baseline_daily_deathrate * AF_heat(T)
#
# We intentionally do not force a 20°C threshold and do not rescale the curves
# to match RR at p99. Validation is against the annual heat excess deaths in
# results/cityage.csv, using the daily ERA5-Land series from additional_data.
#
# Output: reconstructed_rr_curves.csv
#         reconstruction_validation.csv

library(dlnm)
library(splines)

# paths
base_dir <- "/Users/armandeaboudrar-meda/Desktop/CMCC/URBADAPT-HEAT/urban-heat/10288665"

coefs_df <- read.csv(file.path(base_dir, "coefs.csv"), stringsAsFactors = FALSE)
tdist_df <- read.csv(
  file.path(base_dir, "tmean_distribution.csv"),
  check.names = FALSE,
  stringsAsFactors = FALSE
)
cityage_df <- read.csv(
  file.path(base_dir, "results", "cityage.csv"),
  stringsAsFactors = FALSE
)
era5_df <- read.csv(
  file.path(base_dir, "additional_data", "era5series.csv"),
  stringsAsFactors = FALSE
)
era5_df$date <- as.Date(era5_df$date)

# target cities 
our_cities <- data.frame(
  URAU_CODE = c("IT001C", "EL001C", "PT001C", "DK001C"),
  label = c("Rome", "Athens", "Lisbon", "Copenhagen"),
  stringsAsFactors = FALSE
)
age_groups <- c("20-44", "45-64", "65-74", "75-84", "85+")

# helpers 
pct_diff <- function(estimate, target) {
  if (is.na(target) || abs(target) < 1e-12) {
    return(NA_real_)
  }
  100 * (estimate - target) / target
}

make_basis <- function(x, p10, p75, p90, tmin, tmax) {
  onebasis(
    x,
    fun = "bs",
    degree = 2,
    knots = c(p10, p75, p90),
    Boundary.knots = c(tmin, tmax)
  )
}

log_rr_from_basis <- function(basis, basis_mmt, coef_vec) {
  as.numeric(basis %*% coef_vec) - as.numeric(basis_mmt %*% coef_vec)
}

heat_af <- function(rr, temp, mmt, nonnegative = TRUE) {
  af <- (rr - 1) / rr
  af[temp < mmt] <- 0
  if (nonnegative) {
    af <- pmax(0, af)
  }
  af
}

temperature_grid <- function(tmin, tmax) {
  first_half_degree <- ceiling(tmin * 2) / 2
  last_half_degree <- floor(tmax * 2) / 2
  middle <- if (first_half_degree <= last_half_degree) {
    seq(first_half_degree, last_half_degree, by = 0.5)
  } else {
    numeric(0)
  }
  sort(unique(c(tmin, middle, tmax)))
}

# storage
all_rr <- list()
validation <- list()

cat("============================================================\n")
cat("Reconstructing Masselot et al. (2023) RR(T) curves\n")
cat("  ERF: coefs.csv B-spline, centred on Masselot city-age MMT\n")
cat("  Conversion: AF=(RR-1)/RR, then baseline_daily_deathrate * AF\n")
cat("  Validation: annual heat excess deaths vs results/cityage.csv\n")
cat("============================================================\n\n")

for (i in seq_len(nrow(our_cities))) {
  city_code <- our_cities$URAU_CODE[i]
  city_label <- our_cities$label[i]

  # temperature distribution 
  td_row <- tdist_df[tdist_df$URAU_CODE == city_code, ]
  stopifnot(nrow(td_row) == 1)

  tmin <- as.numeric(td_row[, "0.0%"])
  tmax <- as.numeric(td_row[, "100.0%"])
  p10 <- as.numeric(td_row[, "10.0%"])
  p75 <- as.numeric(td_row[, "75.0%"])
  p90 <- as.numeric(td_row[, "90.0%"])
  t99 <- as.numeric(td_row[, "99.0%"])
  t01 <- as.numeric(td_row[, "1.0%"])

  ca_city <- cityage_df[cityage_df$URAU_CODE == city_code, ]
  inmcc <- ca_city$inmcc[1]

  era_city <- era5_df[era5_df$URAU_CODE == city_code, ]
  stopifnot(nrow(era_city) > 0)
  n_years <- length(unique(format(era_city$date, "%Y")))
  era_temp <- pmin(pmax(as.numeric(era_city$era5landtmean), tmin), tmax)

  cat(sprintf("--- %s (%s, inmcc=%s) ---\n", city_label, city_code, inmcc))
  cat(sprintf("  T range: [%.1f, %.1f], p99=%.1f, years=%d\n", tmin, tmax, t99, n_years))

  # temperature grid: 0.5 degree steps within observed ERA5 support 
  T_grid <- temperature_grid(tmin, tmax)

  # basis matrices 
  bvar_grid <- make_basis(T_grid, p10, p75, p90, tmin, tmax)
  bvar_era <- make_basis(era_temp, p10, p75, p90, tmin, tmax)
  stopifnot(ncol(bvar_grid) == 5)

  for (ag in age_groups) {
    # coefficients from coefs.csv 
    cf_row <- coefs_df[
      coefs_df$URAU_CODE == city_code & coefs_df$agegroup == ag,
    ]
    stopifnot(nrow(cf_row) == 1)
    coef_vec <- as.numeric(cf_row[, c("b1", "b2", "b3", "b4", "b5")])

    # pre-computed validation data 
    ca_row <- cityage_df[
      cityage_df$URAU_CODE == city_code & cityage_df$agegroup == ag,
    ]
    stopifnot(nrow(ca_row) == 1)
    mmt <- as.numeric(ca_row$mmt)
    rr_heat_val <- as.numeric(ca_row$rr_heat)
    rr_cold_val <- as.numeric(ca_row$rr_cold)
    baseline_ann <- as.numeric(ca_row$deathrate)
    agepop <- as.numeric(ca_row$agepop)
    heat_deaths_val <- as.numeric(ca_row$excess_heat_est)

    # raw log-RR from B-spline (unscaled)
    bvar_mmt <- make_basis(mmt, p10, p75, p90, tmin, tmax)
    log_rr <- log_rr_from_basis(bvar_grid, bvar_mmt, coef_vec)
    rr <- exp(log_rr)

    # diagnostics at published percentiles 
    bvar_t99 <- make_basis(t99, p10, p75, p90, tmin, tmax)
    bvar_t01 <- make_basis(t01, p10, p75, p90, tmin, tmax)
    rr_raw_t99 <- exp(log_rr_from_basis(bvar_t99, bvar_mmt, coef_vec))
    rr_raw_t01 <- exp(log_rr_from_basis(bvar_t01, bvar_mmt, coef_vec))

    # log-RR slope at tmax for tail extrapolation
    n_grid <- length(T_grid)
    if (n_grid >= 3) {
      i_end   <- n_grid
      i_start <- max(1, n_grid - 2)  # last ~1 C (two 0.5-degree steps)
      dT <- T_grid[i_end] - T_grid[i_start]
      slope_at_tmax <- if (dT > 1e-6) (log_rr[i_end] - log_rr[i_start]) / dT else 0.0
      slope_at_tmax <- max(0.0, slope_at_tmax)  # constrain nonneg
    } else {
      slope_at_tmax <- 0.0
    }
    cat(sprintf("    log_rr_slope_at_tmax = %.6f /degC, tmax = %.2f\n", slope_at_tmax, tmax))

    # convert to CLIMADA-compatible mdd
    af_grid <- heat_af(rr, T_grid, mmt, nonnegative = TRUE)
    baseline_daily_per100k <- baseline_ann * 100000 / 365.25
    baseline_daily_per_person <- baseline_ann / 365.25
    mdd_heat <- baseline_daily_per_person * af_grid
    excess_heat_per100k_day <- baseline_daily_per100k * af_grid

    # annual validation against Masselot cityage.csv 
    log_rr_era <- log_rr_from_basis(bvar_era, bvar_mmt, coef_vec)
    rr_era <- exp(log_rr_era)
    af_era_official <- heat_af(rr_era, era_temp, mmt, nonnegative = FALSE)
    af_era_export <- heat_af(rr_era, era_temp, mmt, nonnegative = TRUE)
    annual_heat_deaths <- mean(af_era_official, na.rm = TRUE) * agepop * baseline_ann
    annual_heat_deaths_export <- mean(af_era_export, na.rm = TRUE) * agepop * baseline_ann
    annual_pct_diff <- pct_diff(annual_heat_deaths, heat_deaths_val)
    annual_export_pct_diff <- pct_diff(annual_heat_deaths_export, heat_deaths_val)

    cat(sprintf(
      "  %s: MMT=%.1f, RR@p99 raw=%.4f vs cityage=%.4f, annual heat=%.3f vs %.3f (%+.2f%%)\n",
      ag,
      mmt,
      rr_raw_t99,
      rr_heat_val,
      annual_heat_deaths,
      heat_deaths_val,
      annual_pct_diff
    ))

    validation[[length(validation) + 1]] <- data.frame(
      city = city_label,
      URAU_CODE = city_code,
      agegroup = ag,
      inmcc = inmcc,
      mmt = mmt,
      rr_heat_raw = rr_raw_t99,
      rr_heat_cityage = rr_heat_val,
      pct_diff_rr_heat = pct_diff(rr_raw_t99, rr_heat_val),
      rr_cold_raw = rr_raw_t01,
      rr_cold_cityage = rr_cold_val,
      pct_diff_rr_cold = pct_diff(rr_raw_t01, rr_cold_val),
      annual_heat_deaths_reconstructed = annual_heat_deaths,
      annual_heat_deaths_export_nonnegative = annual_heat_deaths_export,
      annual_heat_deaths_cityage = heat_deaths_val,
      pct_diff_annual_heat_deaths = annual_pct_diff,
      pct_diff_export_nonnegative = annual_export_pct_diff,
      n_days = nrow(era_city),
      n_years = n_years,
      stringsAsFactors = FALSE
    )

    # store curve
    all_rr[[length(all_rr) + 1]] <- data.frame(
      city = city_label,
      URAU_CODE = city_code,
      agegroup = ag,
      mmt = mmt,
      tmax = tmax,
      log_rr_slope_at_tmax = slope_at_tmax,
      temperature = T_grid,
      rr = rr,
      log_rr = log_rr,
      af_heat = af_grid,
      baseline_daily_per100k = baseline_daily_per100k,
      baseline_daily_per_person = baseline_daily_per_person,
      mdd_heat_per_person_day = mdd_heat,
      excess_heat_only_per100k_day = excess_heat_per100k_day,
      stringsAsFactors = FALSE
    )
  }
  cat("\n")
}

# combine and save 
all_rr_df <- do.call(rbind, all_rr)
validation_df <- do.call(rbind, validation)

out_rr <- file.path(base_dir, "reconstructed_rr_curves.csv")
out_val <- file.path(base_dir, "reconstruction_validation.csv")

write.csv(all_rr_df, out_rr, row.names = FALSE)
write.csv(validation_df, out_val, row.names = FALSE)

cat("============================================================\n")
cat("VALIDATION SUMMARY\n")
cat("============================================================\n")
cat(sprintf(
  "Max |annual heat diff|: %.2f%%\n",
  max(abs(validation_df$pct_diff_annual_heat_deaths), na.rm = TRUE)
))
cat(sprintf(
  "Mean |annual heat diff|: %.2f%%\n",
  mean(abs(validation_df$pct_diff_annual_heat_deaths), na.rm = TRUE)
))
cat("\nAnnual validation:\n")
print(
  validation_df[, c(
    "city",
    "agegroup",
    "annual_heat_deaths_reconstructed",
    "annual_heat_deaths_cityage",
    "pct_diff_annual_heat_deaths"
  )],
  digits = 4,
  row.names = FALSE
)

cat(sprintf("\nSaved %d rows to %s\n", nrow(all_rr_df), out_rr))
cat(sprintf("Saved validation to %s\n", out_val))

# quick sanity: excess deaths/100k/day at key temperatures 
cat("\n============================================================\n")
cat("EXCESS HEAT DEATHS PER 100K PER DAY at key temperatures\n")
cat("============================================================\n")
for (cl in unique(all_rr_df$city)) {
  cat(sprintf("\n--- %s ---\n", cl))
  sub <- all_rr_df[all_rr_df$city == cl, ]
  for (ag in age_groups) {
    sub_ag <- sub[sub$agegroup == ag, ]
    mmt_ag <- sub_ag$mmt[1]
    cat(sprintf("  %s (MMT=%.1f): ", ag, mmt_ag))
    for (tt in c(25, 28, 30, 33, 35)) {
      idx <- which.min(abs(sub_ag$temperature - tt))
      if (length(idx) > 0 && sub_ag$temperature[idx] >= mmt_ag) {
        cat(sprintf("%d°C=%.3f  ", tt, sub_ag$excess_heat_only_per100k_day[idx]))
      }
    }
    cat("\n")
  }
}
