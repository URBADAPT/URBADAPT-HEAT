# ============================================================================
# AS-RUN VERSION of export_emulator_bundle_pooled_142cities.R
# Fit of the pooled LST-T2M emulator used by URBADAPT-HEAT v1.0.1
# (bundle sha256 30750457..., t2m_center 17.0117, n_obs 33,391,358, 130 cities).
# Reconstructed from the R history of the interactive session in which the
# bundle and the leave-one-city-out validation were produced (19-20 May 2026,
# Windows workstation, 64 GB RAM): the first lines of the script that fell out
# of the 512-line history are taken from the 25 May copy of the script; three
# abandoned fit attempts that ran out of memory or errored are omitted.
# Settings as run: POOL_SAMPLE_FRAC = 0.33 (stratified by LCZ, min 5 points per
# stratum, seed 42) at load time, then a 10% subsample per city x LCZ before
# the fit. The header comments below about "no point sampling" are stale.
# Verbatim session history: ../session/R_history_2026-05-19_20.txt
# ============================================================================
# export_emulator_bundle_pooled_142cities.R
# Pooled emulator across all 142 PROVIDE cities
#
# PURPOSE:
#   Train a single holdout-safe quadratic emulator on pooled data from all
#   PROVIDE cities for which both LST/T2M data and GVI points exist.
#   No point sampling: uses ALL GVI points per city.
#
# FOR SUPERCOMPUTER EXECUTION.
#
# OUTPUTS:
#   - emulator_bundle_pooled_all_quadratic_holdout_safe.json  (for NB07)
#   - emulator_model_pooled_all_quadratic_holdout_safe.rds    (full fixest model)
#   - pooled_all_city_holdout_evaluation.csv                  (LOO validation)
#   - pooled_all_city_holdout_summary.csv                     (aggregated metrics)
#   - pooled_all_panel_diagnostics.csv                        (per-city panel sizes)
#
# DATA REQUIREMENTS (must be in working directory or paths adjusted):
#   - after_points_100425_citynames.rds   (GVI points for all cities)
#   - cities_provide.csv                  (list of PROVIDE city names)
#   - lcz_filter_v3.tif                   (LCZ raster)
#   - GHS_BUILT_H_AGBH_E2018_GLOBE_R2023A_4326_3ss_V1_0.tif
#   - GHS_BUILT_V_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif
#   - GHS_POP_E2020_GLOBE_R2023A_4326_3ss_V1_0.tif
#   - c_gls_WB300_202306010000_GLOBE_S2_V2.0.1.nc
#   - Internet access for PROVIDE API (LST/T2M downloads) and elevatr (elevation)
#
# USAGE:
#   cd /path/to/working_directory
#   Rscript export_emulator_bundle_pooled_142cities.R
#
# Source the validation script (brings in load_or_build_city_panel, helpers, etc.)
# The sys.nframe() guard in the multicities script prevents it from running Rome.
source("estimate_climate_emulator_validation.R", local = FALSE)
library(dplyr)
library(data.table)   # rbindlist + in-place := for memory-efficient pooling
library(fixest)
library(jsonlite)
# Columns the pooled model actually needs from each cached panel.
# Anything not in this list (x, y, year, ym, doy, sin_doy, cos_doy, log_*,
# t2m_c, city_point_id, ...) is dropped immediately at readRDS time to keep
# peak RAM small. Derived columns (month, log_*, t2m_c, city_point_id) are
# recomputed once on the pooled panel.
MODEL_COLS <- c("point_id", "date", "lst", "t2m",
"out_b", "build_h", "build_v", "pop_dens",
"water", "elevation", "lcz")
# CONFIGURATION
# Read PROVIDE city list from CSV
PROVIDE_CITIES_FILE <- "cities_provide.csv"
if (!file.exists(PROVIDE_CITIES_FILE)) {
stop("Cannot find ", PROVIDE_CITIES_FILE, " in working directory: ", getwd())
}
cities_df <- read.csv(PROVIDE_CITIES_FILE, stringsAsFactors = FALSE)
POOLED_CITIES <- trimws(cities_df$city)
POOLED_CITIES <- POOLED_CITIES[nchar(POOLED_CITIES) > 0]
cat("PROVIDE cities to process: ", length(POOLED_CITIES), "\n")
DEPLOY_YEARS <- 2008:2017
# NO legacy point cap — use the stratified-fraction sampler below instead.
POINTS_PER_CITY <- NULL
# Stratified per-city point subsample (memory management).
# At load time, keep POOL_SAMPLE_FRAC of GVI points per city, stratified by
# POOL_STRAT_COL (LCZ). All days are retained for each kept point so the
# panel's temporal structure is preserved. Set POOL_SAMPLE_FRAC <- NULL or 1
# to disable.
POOL_SAMPLE_FRAC      <- 0.33
POOL_STRAT_COL        <- "lcz"
POOL_MIN_PER_STRATUM  <- 5
POOL_SAMPLE_SEED      <- 42
OUT_DIR <- "outputs/emulator/bundles"
dir.create(OUT_DIR, showWarnings = FALSE, recursive = TRUE)
# 1. Load all city panels (download + build + cache)
cat("\n")
cat("================================================================================\n")
cat("  POOLED ALL-CITY EMULATOR\n")
cat("  Cities requested: ", length(POOLED_CITIES), "\n")
cat("  Point sampling:   ",
if (is.null(POOL_SAMPLE_FRAC) || POOL_SAMPLE_FRAC >= 1) {
"DISABLED (all points per city)"
} else {
sprintf("frac=%g, stratified by %s (min %d/stratum)",
POOL_SAMPLE_FRAC, POOL_STRAT_COL, POOL_MIN_PER_STRATUM)
}, "\n")
cat("================================================================================\n\n")
POOLED_CITIES <- list.files("Z:/Falchetta/emulator-142cities/outputs/emulator/validation/panel_cache")
POOLED_CITIES <- sub("_LSTmax_T2Mmean_2008_2017_panel_full\\.rds$", "", POOLED_CITIES)
all_panels <- list()
panel_diagnostics <- data.frame()
for (city in POOLED_CITIES) {
cat("Loading panel for ", city, "...\n")
tryCatch({
df <- load_or_build_city_panel(
city,
years = DEPLOY_YEARS,
apply_sampling = FALSE,            # bypass legacy point cap
cols_keep      = MODEL_COLS,        # drop unused columns at readRDS time
sample_frac    = POOL_SAMPLE_FRAC,  # stratified per-city point subsample
sample_strat_col       = POOL_STRAT_COL,
sample_min_per_stratum = POOL_MIN_PER_STRATUM,
sample_seed            = POOL_SAMPLE_SEED
)
if (!is.null(df)) {
# Convert to data.table for in-place ops (avoids dplyr copies during
# the pooling/preprocessing steps below).
data.table::setDT(df)
df[, city := city]
if (!"city_point_id" %in% names(df)) {
df[, city_point_id := paste(city, point_id, sep = "_")]
}
n_pts <- data.table::uniqueN(df$point_id)
n_obs <- nrow(df)
cat("  ", city, ": ", format(n_obs, big.mark = ","), " obs, ",
n_pts, " points (",
if (is.null(POOL_SAMPLE_FRAC) || POOL_SAMPLE_FRAC >= 1) "all kept"
else sprintf("%g%% stratified by %s", 100 * POOL_SAMPLE_FRAC, POOL_STRAT_COL),
")\n")
panel_diagnostics <- rbind(panel_diagnostics, data.frame(
city = city,
n_points = n_pts,
n_obs = n_obs,
status = "OK",
stringsAsFactors = FALSE
))
all_panels[[city]] <- df
} else {
cat("  ", city, ": SKIPPED (no GVI points or no climate data)\n")
panel_diagnostics <- rbind(panel_diagnostics, data.frame(
city = city,
n_points = NA,
n_obs = NA,
status = "SKIPPED_NULL",
stringsAsFactors = FALSE
))
}
}, error = function(e) {
cat("  ", city, ": FAILED (", e$message, ")\n")
panel_diagnostics <<- rbind(panel_diagnostics, data.frame(
city = city,
n_points = NA,
n_obs = NA,
status = paste0("FAILED: ", e$message),
stringsAsFactors = FALSE
))
})
gc()  # free memory between cities
}
# Save diagnostics
diag_file <- file.path(OUT_DIR, "pooled_all_panel_diagnostics.csv")
write.csv(panel_diagnostics, diag_file, row.names = FALSE)
cat("\nSaved panel diagnostics: ", diag_file, "\n")
available_cities <- names(all_panels)
cat("\nAvailable cities (", length(available_cities), "/",
length(POOLED_CITIES), "): \n")
cat("  ", paste(available_cities, collapse = ", "), "\n\n")
skipped <- setdiff(POOLED_CITIES, available_cities)
if (length(skipped) > 0) {
cat("Skipped cities (", length(skipped), "): ",
paste(skipped, collapse = ", "), "\n\n")
}
if (length(available_cities) < 5) {
stop("Too few cities loaded (", length(available_cities),
"). Need at least 5 for a robust pooled model.")
}
# 2. Pool and preprocess
#
# Use data.table::rbindlist (lower peak RAM than dplyr::bind_rows because it
# avoids intermediate copies) and immediately drop `all_panels` so we don't
# keep two full copies of the data alive at the same time.
panel_df <- data.table::rbindlist(all_panels, use.names = TRUE, fill = TRUE)
rm(all_panels); gc()
cat("Pooled panel: ", format(nrow(panel_df), big.mark = ","), " obs across ",
length(available_cities), " cities\n")
# Sanity checks
stopifnot("city_point_id" %in% names(panel_df))
stopifnot("date" %in% names(panel_df))
stopifnot("lst" %in% names(panel_df))
stopifnot("t2m" %in% names(panel_df))
stopifnot("lcz" %in% names(panel_df))
# Preprocess in place with := (no copy per added column, unlike mutate)
panel_df[, month        := as.integer(format(date, "%m"))]
panel_df[, log_pop_dens := log1p(pop_dens)]
panel_df[, log_build_h  := log1p(build_h)]
panel_df[, log_build_v  := log1p(build_v)]
# Drop raw columns we no longer need (logs and month replace them).
panel_df[, c("date", "pop_dens", "build_h", "build_v") := NULL]
gc()
# Center T2M on pooled mean
train_mean_t2m <- mean(panel_df$t2m, na.rm = TRUE)
cat("Pooled T2M centering mean: ", round(train_mean_t2m, 4), " C\n")
panel_df[, t2m_c := t2m - train_mean_t2m]
# Factor LCZ (in place)
panel_df[, lcz := factor(lcz)]
reference_lcz <- levels(panel_df$lcz)[1]
panel_df[, lcz := relevel(lcz, ref = reference_lcz)]
cat("Reference LCZ: ", reference_lcz, "\n")
cat("LCZ levels present: ", paste(levels(panel_df$lcz), collapse = ", "), "\n\n")
# 3. Fit pooled holdout-safe quadratic model
cat("Fitting pooled holdout-safe quadratic model...\n")
cat("  N observations: ", format(nrow(panel_df), big.mark = ","), "\n")
cat("  N cities: ", length(available_cities), "\n")
cat("  N unique points: ", format(n_distinct(panel_df$city_point_id), big.mark = ","), "\n\n")
gc()
# The next call (result discarded) was executed in the original session; it is kept because it
# advances the random-number state, so the assigned 10% subsample below is the one actually used.
panel_df[, .SD[sample(.N, ceiling(.N * 0.1))], by = .(city, lcz)]
panel_df <- panel_df[, .SD[sample(.N, ceiling(.N * 0.1))], by = .(city, lcz)]
model <- feols(
lst ~ t2m_c + I(t2m_c^2) +
out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
data = panel_df,
cluster = ~city_point_id
)
print(summary(model))
# Quick in-sample evaluation
pred <- predict(model, newdata = panel_df)
ok <- is.finite(pred) & is.finite(panel_df$lst)
y <- panel_df$lst[ok]; yhat <- pred[ok]
cat("\n--- In-sample metrics (pooled) ---\n")
cat("  N:    ", format(length(y), big.mark = ","), "\n")
cat("  RMSE: ", round(sqrt(mean((y - yhat)^2)), 3), "\n")
cat("  MAE:  ", round(mean(abs(y - yhat)), 3), "\n")
cat("  R2:   ", round(1 - sum((y - yhat)^2) / sum((y - mean(y))^2), 4), "\n\n")
# 4. Extract coefficients -> bundle JSON
cf <- coef(model)
cat("Coefficient names: ", paste(names(cf), collapse = ", "), "\n\n")
quad_name <- intersect(c("I(I(t2m_c^2))", "I(t2m_c^2)"), names(cf))[1]
beta_lcz_idx <- grep("^t2m_c:lcz", names(cf))
bundle <- list(
model_name    = "pooled_all_quadratic_holdout_safe",
cities        = available_cities,
n_cities      = length(available_cities),
years         = as.integer(DEPLOY_YEARS),
n_obs         = nrow(panel_df),
t2m_center    = unname(train_mean_t2m),
reference_lcz = reference_lcz,
beta_t2m_c       = if ("t2m_c" %in% names(cf)) unname(cf["t2m_c"]) else 0,
beta_t2m_c2      = if (!is.na(quad_name)) unname(cf[quad_name]) else 0,
beta_t2m_c_out_b = if ("t2m_c:out_b" %in% names(cf)) unname(cf["t2m_c:out_b"]) else 0,
beta_t2m_c_water = if ("t2m_c:water" %in% names(cf)) unname(cf["t2m_c:water"]) else 0,
beta_t2m_c_lcz   = as.list(unname(cf[beta_lcz_idx]))
)
names(bundle$beta_t2m_c_lcz) <- gsub("^t2m_c:lcz", "", names(cf[beta_lcz_idx]))
# Sanity: beta_t2m_c_out_b MUST be negative (vegetation cools on hot days)
cat("=== KEY COEFFICIENT CHECK ===\n")
cat("  beta_t2m_c       = ", round(bundle$beta_t2m_c, 4), "\n")
cat("  beta_t2m_c2      = ", round(bundle$beta_t2m_c2, 4), "\n")
cat("  beta_t2m_c_out_b = ", round(bundle$beta_t2m_c_out_b, 4), "\n")
if (bundle$beta_t2m_c_out_b > 0) {
warning("beta_t2m_c_out_b is POSITIVE (", round(bundle$beta_t2m_c_out_b, 4),
"). This means vegetation INCREASES LST on hot days -- physically wrong! ",
"Check data quality or model specification.")
} else {
cat("  >> GOOD: beta_t2m_c_out_b is negative (vegetation cools on hot days)\n")
}
# 5. Save bundle and model
bundle_json_path <- file.path(OUT_DIR, "emulator_bundle_pooled_all_quadratic_holdout_safe.json")
model_rds_path   <- file.path(OUT_DIR, "emulator_model_pooled_all_quadratic_holdout_safe.rds")
write_json(
bundle,
path = bundle_json_path,
auto_unbox = TRUE,
pretty = TRUE
)
cat("\nSaved bundle:  ", bundle_json_path, "\n")
saveRDS(model, model_rds_path)
cat("Saved model:   ", model_rds_path, "\n")
# Free memory before LOO — but KEEP panel_df. The LOO loop below reuses the
# already-pooled, already-preprocessed panel via feols' `subset` argument,
# which avoids both (a) reloading 142 cached RDS files and (b) building a
# fresh per-fold training copy via bind_rows.
rm(model, pred, y, yhat)
gc()
# 6. City-holdout validation (leave-one-out)
#
# NOTE: LOO on 142 cities with full points is very heavy. The pooled panel
# built above is reused directly — feols() takes a `subset` argument, so no
# per-fold training copy is ever materialised, and the cached RDS files are
# NOT reloaded a second time. If RAM is still tight, set LOO_MAX_POINTS to
# subsample points per city before the LOO loop.
#
# To skip LOO entirely (just produce the bundle), set SKIP_LOO <- TRUE.
SKIP_LOO <- FALSE
LOO_MAX_POINTS <- NULL  # Set to e.g. 500 to sample points for LOO only
if (!SKIP_LOO) {
cat("\n")
cat("================================================================================\n")
cat("  CITY-HOLDOUT VALIDATION (", length(available_cities), "-city LOO)\n")
cat("  Point sampling for LOO: ",
if (is.null(LOO_MAX_POINTS)) "DISABLED (all points)" else paste0(LOO_MAX_POINTS, " pts/city"),
"\n")
cat("================================================================================\n\n")
cat("Running LOO directly on the pooled panel (no reload, no per-fold copy)...\n")
# Optional: subsample points once for the whole LOO loop, instead of building
# a parallel `loo_panels` list. Sampling is stratified by city.
if (!is.null(LOO_MAX_POINTS)) {
cat("Subsampling to <= ", LOO_MAX_POINTS, " points per city for LOO...\n", sep = "")
set.seed(42)
keep_keys <- panel_df[, .(kk = {
u <- unique(city_point_id)
if (length(u) > LOO_MAX_POINTS) sample(u, LOO_MAX_POINTS) else u
}), by = city]$kk
panel_df <- panel_df[city_point_id %in% keep_keys]
gc()
cat("  Pooled panel after subsample: ",
format(nrow(panel_df), big.mark = ","), " obs\n")
}
all_eval <- data.frame()
for (held_out in available_cities) {
cat("  Holding out: ", held_out, " ... ")
# Logical mask — does NOT copy the data; feols' `subset` arg uses it directly.
train_mask <- panel_df$city != held_out
n_test <- sum(!train_mask)
if (n_test < 100) {
cat("SKIPPED (too few obs)\n")
next
}
# Re-center T2M on training rows; update t2m_c in place (single column write,
# not a data copy).
train_mean <- mean(panel_df$t2m[train_mask], na.rm = TRUE)
panel_df[, t2m_c := t2m - train_mean]
# Fit holdout-safe quadratic on the training subset.
# `subset = train_mask` avoids constructing a separate train_df.
m_loo <- tryCatch(
feols(
lst ~ t2m_c + I(t2m_c^2) +
out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
data = panel_df,
subset = train_mask,
cluster = ~city_point_id
),
error = function(e) { warning(e$message); NULL }
)
if (is.null(m_loo)) {
cat("FAILED\n")
gc()
next
}
# Evaluate on held-out city (single-city slice is small, copy is cheap).
test_df <- panel_df[!train_mask]
res <- eval_model_on_data(m_loo, test_df, "m3_quadratic")
res$held_out_city <- held_out
res$train_cities  <- paste(setdiff(available_cities, held_out), collapse = "+")
# Record GVI interaction coefficient
cf_loo <- coef(m_loo)
res$beta_t2m_c_out_b <- if ("t2m_c:out_b" %in% names(cf_loo)) unname(cf_loo["t2m_c:out_b"]) else NA
all_eval <- rbind(all_eval, res)
cat("R2=", round(res$r2_pred, 3), " RMSE=", round(res$rmse, 2),
" beta_GVI=", round(res$beta_t2m_c_out_b, 3), "\n")
rm(test_df, m_loo)
gc()
}
# Done with the pooled panel
rm(panel_df)
gc()
# Summary
cat("\n--- City-Holdout Summary ---\n\n")
if (nrow(all_eval) == 0) {
cat("  WARNING: All LOO folds failed. No validation results.\n")
} else {
summary_by_city <- all_eval %>%
select(held_out_city, n, rmse, mae, r2_pred, rmse_hot, beta_t2m_c_out_b)
print(summary_by_city)
cat("\n--- Aggregate ---\n")
cat("  N cities evaluated: ", nrow(all_eval), "\n")
cat("  Mean R2:   ", round(mean(all_eval$r2_pred, na.rm = TRUE), 4), "\n")
cat("  Mean RMSE: ", round(mean(all_eval$rmse, na.rm = TRUE), 3), "\n")
cat("  Mean MAE:  ", round(mean(all_eval$mae, na.rm = TRUE), 3), "\n")
cat("  Mean RMSE_hot: ", round(mean(all_eval$rmse_hot, na.rm = TRUE), 3), "\n")
cat("  beta_GVI range: [",
round(min(all_eval$beta_t2m_c_out_b, na.rm = TRUE), 3), ", ",
round(max(all_eval$beta_t2m_c_out_b, na.rm = TRUE), 3), "]\n")
# Check GVI interaction sign
if (any(all_eval$beta_t2m_c_out_b > 0, na.rm = TRUE)) {
bad <- all_eval$held_out_city[all_eval$beta_t2m_c_out_b > 0]
warning("beta_t2m_c_out_b positive in folds holding out: ",
paste(bad, collapse = ", "))
} else {
cat("  >> GOOD: GVI interaction negative in ALL LOO folds\n")
}
# Save validation results
eval_file <- file.path(OUT_DIR, "pooled_all_city_holdout_evaluation.csv")
write.csv(all_eval, eval_file, row.names = FALSE)
cat("\nSaved: ", eval_file, "\n")
summary_file <- file.path(OUT_DIR, "pooled_all_city_holdout_summary.csv")
write.csv(summary_by_city, summary_file, row.names = FALSE)
cat("Saved: ", summary_file, "\n")
}
SKIP_LOO <- FALSE
LOO_MAX_POINTS <- NULL  # Set to e.g. 500 to sample points for LOO only
if (!SKIP_LOO) {
cat("\n")
cat("================================================================================\n")
cat("  CITY-HOLDOUT VALIDATION (", length(available_cities), "-city LOO)\n")
cat("  Point sampling for LOO: ",
if (is.null(LOO_MAX_POINTS)) "DISABLED (all points)" else paste0(LOO_MAX_POINTS, " pts/city"),
"\n")
cat("================================================================================\n\n")
cat("Running LOO directly on the pooled panel (no reload, no per-fold copy)...\n")
# Optional: subsample points once for the whole LOO loop, instead of building
# a parallel `loo_panels` list. Sampling is stratified by city.
if (!is.null(LOO_MAX_POINTS)) {
cat("Subsampling to <= ", LOO_MAX_POINTS, " points per city for LOO...\n", sep = "")
set.seed(42)
keep_keys <- panel_df[, .(kk = {
u <- unique(city_point_id)
if (length(u) > LOO_MAX_POINTS) sample(u, LOO_MAX_POINTS) else u
}), by = city]$kk
panel_df <- panel_df[city_point_id %in% keep_keys]
gc()
cat("  Pooled panel after subsample: ",
format(nrow(panel_df), big.mark = ","), " obs\n")
}
all_eval <- data.frame()
for (held_out in available_cities) {
cat("  Holding out: ", held_out, " ... ")
# Logical mask — does NOT copy the data; feols' `subset` arg uses it directly.
train_mask <- panel_df$city != held_out
n_test <- sum(!train_mask)
if (n_test < 100) {
cat("SKIPPED (too few obs)\n")
next
}
# Re-center T2M on training rows; update t2m_c in place (single column write,
# not a data copy).
train_mean <- mean(panel_df$t2m[train_mask], na.rm = TRUE)
panel_df[, t2m_c := t2m - train_mean]
# Fit holdout-safe quadratic on the training subset.
# `subset = train_mask` avoids constructing a separate train_df.
m_loo <- tryCatch(
feols(
lst ~ t2m_c + I(t2m_c^2) +
out_b + log_build_h + log_build_v + log_pop_dens + water + elevation +
t2m_c:out_b + t2m_c:water + t2m_c:lcz | month + lcz,
data = panel_df,
subset = train_mask,
cluster = ~city_point_id
),
error = function(e) { warning(e$message); NULL }
)
if (is.null(m_loo)) {
cat("FAILED\n")
gc()
next
}
# Evaluate on held-out city (single-city slice is small, copy is cheap).
test_df <- panel_df[!train_mask]
res <- eval_model_on_data(m_loo, test_df, "m3_quadratic")
res$held_out_city <- held_out
res$train_cities  <- paste(setdiff(available_cities, held_out), collapse = "+")
# Record GVI interaction coefficient
cf_loo <- coef(m_loo)
res$beta_t2m_c_out_b <- if ("t2m_c:out_b" %in% names(cf_loo)) unname(cf_loo["t2m_c:out_b"]) else NA
all_eval <- rbind(all_eval, res)
cat("R2=", round(res$r2_pred, 3), " RMSE=", round(res$rmse, 2),
" beta_GVI=", round(res$beta_t2m_c_out_b, 3), "\n")
rm(test_df, m_loo)
gc()
}
# Done with the pooled panel
rm(panel_df)
gc()
# Summary
cat("\n--- City-Holdout Summary ---\n\n")
if (nrow(all_eval) == 0) {
cat("  WARNING: All LOO folds failed. No validation results.\n")
} else {
summary_by_city <- all_eval %>%
select(held_out_city, n, rmse, mae, r2_pred, rmse_hot, beta_t2m_c_out_b)
print(summary_by_city)
cat("\n--- Aggregate ---\n")
cat("  N cities evaluated: ", nrow(all_eval), "\n")
cat("  Mean R2:   ", round(mean(all_eval$r2_pred, na.rm = TRUE), 4), "\n")
cat("  Mean RMSE: ", round(mean(all_eval$rmse, na.rm = TRUE), 3), "\n")
cat("  Mean MAE:  ", round(mean(all_eval$mae, na.rm = TRUE), 3), "\n")
cat("  Mean RMSE_hot: ", round(mean(all_eval$rmse_hot, na.rm = TRUE), 3), "\n")
cat("  beta_GVI range: [",
round(min(all_eval$beta_t2m_c_out_b, na.rm = TRUE), 3), ", ",
round(max(all_eval$beta_t2m_c_out_b, na.rm = TRUE), 3), "]\n")
# Check GVI interaction sign
if (any(all_eval$beta_t2m_c_out_b > 0, na.rm = TRUE)) {
bad <- all_eval$held_out_city[all_eval$beta_t2m_c_out_b > 0]
warning("beta_t2m_c_out_b positive in folds holding out: ",
paste(bad, collapse = ", "))
} else {
cat("  >> GOOD: GVI interaction negative in ALL LOO folds\n")
}
# Save validation results
eval_file <- file.path(OUT_DIR, "pooled_all_city_holdout_evaluation.csv")
write.csv(all_eval, eval_file, row.names = FALSE)
cat("\nSaved: ", eval_file, "\n")
summary_file <- file.path(OUT_DIR, "pooled_all_city_holdout_summary.csv")
write.csv(summary_by_city, summary_file, row.names = FALSE)
cat("Saved: ", summary_file, "\n")
}
} # end if (!SKIP_LOO)
