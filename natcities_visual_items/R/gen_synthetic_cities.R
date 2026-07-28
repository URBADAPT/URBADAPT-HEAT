# =============================================================================
# gen_synthetic_cities.R  --  synthetic 40-city dataset for FIGURE PLANNING ONLY
# -----------------------------------------------------------------------------
# Generates plausible, climate-correlated PLACEHOLDER results for every city in
# the study so the Nature Cities cross-city figures can be designed at their
# intended ~40-city scale *before* the real model runs land. Values are fake but
# internally consistent and span the real European heat gradient (Seville/Athens
# hot -> Helsinki/Dublin cool), so k-means yields three populated clusters and
# every panel fills in.
#
# SAFETY / ISOLATION
#   * Writes ONLY under the synthetic variant dir
#       urban-heat/outputs_variants/<VARIANT>/   (VARIANT defaults to
#       "masselot_main_agnostic_synthetic"; the real variant is never touched).
#   * Real Milan & Rome outputs are COPIED in verbatim (kept real, as a
#     magnitude sanity-check next to the synthetic cities).
#   * Every generated city dir gets a `.SYNTHETIC` marker; a manifest is written
#     to <TAB_DIR>/.synthetic_manifest.csv. Use clean_synthetic.R to remove.
#
# Sourced by build_synthetic.R (which sets the env vars first). Standalone use:
#   Sys.setenv(NATCITIES_VARIANT="masselot_main_agnostic_synthetic")
#   source("_helpers.R"); source("gen_synthetic_cities.R"); generate_synthetic()
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}
# assign_clusters()/read_config() live in 00_city_meta.R; source without running.
local({
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  .prev <- if (exists(".NATCITIES_NORUN")) get(".NATCITIES_NORUN") else NULL
  assign(".NATCITIES_NORUN", TRUE, envir = .GlobalEnv)
  source(file.path(.d, "00_city_meta.R"), local = FALSE)
})
suppressPackageStartupMessages(library(jsonlite))

# Slugs of cities whose REAL outputs exist and should be copied, not faked.
REAL_CITIES <- c("milan", "rome")

# ---- city profiles ----------------------------------------------------------
# city | iso3 | country | pop_k | warm-season mean T2M (degC) | hot-day count.
# W and H place each city on a realistic May-Sep heat gradient. For the two real
# cities W/H here are only fallbacks; their true cached metrics override below.
.PROF <- function(city, iso3, country, pop_k, W, H)
  data.frame(city = city, iso3 = iso3, country = country, pop_k = pop_k,
             W = W, H = H, stringsAsFactors = FALSE)

PROFILES <- do.call(rbind, list(
  .PROF("amsterdam",   "NLD", "Netherlands", 1150, 17.2, 12),
  .PROF("athens",      "GRC", "Greece",      3150, 26.8, 85),
  .PROF("barcelona",   "ESP", "Spain",       4800, 24.0, 58),
  .PROF("berlin",      "DEU", "Germany",     3600, 19.5, 26),
  .PROF("bologna",     "ITA", "Italy",       1000, 23.0, 52),
  .PROF("bratislava",  "SVK", "Slovakia",     660, 20.8, 34),
  .PROF("brussels",    "BEL", "Belgium",     2080, 17.5, 14),
  .PROF("bucharest",   "ROU", "Romania",     2160, 22.5, 48),
  .PROF("budapest",    "HUN", "Hungary",     1750, 21.8, 40),
  .PROF("cologne",     "DEU", "Germany",     1080, 18.8, 22),
  .PROF("copenhagen",  "DNK", "Denmark",     1340, 17.0, 11),
  .PROF("dublin",      "IRL", "Ireland",     1420, 15.2,  4),
  .PROF("hamburg",     "DEU", "Germany",     1840, 17.5, 14),
  .PROF("helsinki",    "FIN", "Finland",     1300, 16.2,  8),
  .PROF("lisbon",      "PRT", "Portugal",    2960, 23.5, 55),
  .PROF("ljubljana",   "SVN", "Slovenia",     290, 20.5, 32),
  .PROF("lyon",        "FRA", "France",      1650, 21.0, 35),
  .PROF("madrid",      "ESP", "Spain",       6640, 25.2, 72),
  .PROF("marseille",   "FRA", "France",      1600, 23.3, 52),
  .PROF("milan",       "ITA", "Italy",       1370, 22.733, 48),
  .PROF("munich",      "DEU", "Germany",     1560, 18.5, 20),
  .PROF("nantes",      "FRA", "France",       950, 19.5, 24),
  .PROF("naples",      "ITA", "Italy",       3080, 25.0, 70),
  .PROF("palermo",     "ITA", "Italy",        850, 26.0, 78),
  .PROF("paris",       "FRA", "France",     11000, 19.8, 27),
  .PROF("porto",       "PRT", "Portugal",    1720, 20.5, 30),
  .PROF("prague",      "CZE", "Czechia",     1320, 19.5, 26),
  .PROF("riga",        "LVA", "Latvia",       640, 17.2, 13),
  .PROF("rome",        "ITA", "Italy",       2800, 23.244, 65),
  .PROF("rotterdam",   "NLD", "Netherlands", 1180, 17.3, 13),
  .PROF("sevilla",     "ESP", "Spain",       1540, 27.5, 92),
  .PROF("sofia",       "BGR", "Bulgaria",    1280, 21.5, 40),
  .PROF("stockholm",   "SWE", "Sweden",      1660, 17.0, 12),
  .PROF("tallinn",     "EST", "Estonia",      440, 16.0,  8),
  .PROF("thessaloniki","GRC", "Greece",      1010, 24.8, 68),
  .PROF("varna",       "BGR", "Bulgaria",     380, 22.0, 42),
  .PROF("vienna",      "AUT", "Austria",     1930, 20.5, 33),
  .PROF("vilnius",     "LTU", "Lithuania",    580, 17.5, 15),
  .PROF("warsaw",      "POL", "Poland",      3100, 19.8, 28),
  .PROF("zagreb",      "HRV", "Croatia",      810, 21.5, 38)
))

# ---- deterministic per-city RNG --------------------------------------------
city_seed <- function(city) sum(utf8ToInt(city)) * 7L + 13L
clamp <- function(x, lo, hi) pmin(hi, pmax(lo, x))
# one N(mu,sd) draw, clamped
jit <- function(mu, sd, lo = -Inf, hi = Inf) clamp(rnorm(1, mu, sd), lo, hi)

# ---- per-city generators ----------------------------------------------------
# All quantities are functions of warm-season T2M (W), hot days (H) and pop (P,
# thousands), plus reproducible noise. Anchored loosely to Milan's real values.

YEARS <- c(2020, 2030, 2040, 2050)

gen_city_files <- function(row) {
  city <- row$city; P <- row$pop_k; W <- row$W; H <- row$H
  set.seed(city_seed(city))
  tdir <- city_path(city, where = "tables"); rdir <- city_path(city, where = "root")
  dir.create(tdir, showWarnings = FALSE, recursive = TRUE)

  # --- baseline heat mortality trajectory (2020..2050) -----------------------
  rate_100k <- jit(8 + 1.15 * (W - 14)^1.35, 2.5, 6, 62)   # deaths/100k in 2020
  deaths_2020 <- rate_100k * P / 100
  decade_growth <- jit(1.18 + 0.004 * (W - 20), 0.02, 1.08, 1.30)
  deaths_series <- deaths_2020 * decade_growth^((YEARS - 2020) / 10)
  frac0 <- jit(0.42 + 0.012 * (W - 16), 0.03, 0.30, 0.62)
  frac_series <- clamp(frac0 + (0.86 - frac0) * ((YEARS - 2020) / 30), 0.30, 0.90)
  nwd0 <- max(4, round(H * 0.5 + rnorm(1, 0, 3)))
  nwd_series <- round(nwd0 * (1.22^((YEARS - 2020) / 10)))
  base_df <- data.frame(
    year = YEARS,
    deaths_overall = deaths_series,
    deaths_on_warning_days = deaths_series * frac_series,
    frac_deaths_on_warning_days = frac_series,
    n_warning_days = nwd_series)
  readr::write_csv(base_df,
    file.path(tdir, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", city)))

  # 25-year cumulative baseline deaths (for % -> avoided conversions)
  ann_factor <- sum(decade_growth^((0:24) / 10))
  baseline_25y <- deaths_2020 * ann_factor

  # --- policy effectiveness --------------------------------------------------
  pct_trees <- jit(1.0 + 0.055 * (W - 16), 0.20, 0.5, 2.6)
  pct_ac_gross <- jit(4.5 + 0.18 * (W - 16), 0.35, 3.2, 9.5)
  erosion <- jit(0.08 + 0.011 * (W - 16), 0.012, 0.05, 0.34)   # waste-heat share
  pct_ac_net <- pct_ac_gross * (1 - erosion)
  pct_ews <- jit(4.8 + 0.03 * (W - 16), 0.30, 3.5, 6.6)
  av_trees <- baseline_25y * pct_trees / 100
  av_ac_gross <- baseline_25y * pct_ac_gross / 100
  av_ac_net <- baseline_25y * pct_ac_net / 100
  av_ews <- baseline_25y * pct_ews / 100
  eff_df <- data.frame(
    policy = c("Trees (uniform)", "AC (GROSS)", "AC (NET)", "EWS (marginal)"),
    avoided_deaths_25y = c(av_trees, av_ac_gross, av_ac_net, av_ews),
    pct_reduction = c(pct_trees, pct_ac_gross, pct_ac_net, pct_ews),
    deaths_per_100k_year =
      c(av_trees, av_ac_gross, av_ac_net, av_ews) / 25 * 100 / P,
    interpretation = c("Hazard modification (cooling)",
                       "Exposure reduction (indoor cooling)",
                       "Net of waste heat feedback",
                       "Vulnerability reduction (behavior)"))
  readr::write_csv(eff_df, file.path(tdir, sprintf("%s_policy_effectiveness.csv", city)))

  # --- public / private costs ------------------------------------------------
  cpc_trees <- jit(150 + 4 * (W - 16), 20, 90, 240)     # euro / capita
  cpc_ac <- jit(620 + 42 * (W - 16), 70, 380, 1450)
  cpc_ews <- jit(15, 2.5, 8, 24)
  trees_pv <- cpc_trees * P * 1000
  ac_pv <- cpc_ac * P * 1000
  ews_pv <- cpc_ews * P * 1000
  ac_share <- jit(0.14 - 0.004 * (W - 16), 0.02, 0.05, 0.22)
  costs_df <- data.frame(
    policy = c("Trees", "AC", "EWS"),
    total_pv = c(trees_pv, ac_pv, ews_pv),
    public_pv = c(trees_pv, ac_pv * ac_share, ews_pv),
    private_pv = c(0, ac_pv * (1 - ac_share), 0),
    public_share = c(1.0, ac_share, 1.0))
  readr::write_csv(costs_df, file.path(tdir, sprintf("%s_public_private_costs.csv", city)))

  # --- avoided deaths: uniform vs income-targeted AC roll-out ----------------
  uni_base <- av_ac_net / 25 * jit(1.15, 0.05, 0.9, 1.4)
  targ_ratio <- jit(0.88, 0.10, 0.62, 1.12)   # <1 = targeting costs total benefit
  ad_df <- do.call(rbind, lapply(YEARS, function(y) {
    g <- decade_growth^((y - 2020) / 10)
    u <- uni_base * g * jit(1, 0.04)
    data.frame(year = c(y, y), policy = c("income-targeted", "uniform"),
               avoided_deaths = c(u * targ_ratio, u))
  }))
  readr::write_csv(ad_df, file.path(rdir, sprintf("%s_avoided_deaths_summary.csv", city)))

  # --- district greening vs vulnerability ------------------------------------
  n_d <- clamp(round(P / 180) + 4, 4, 14)
  svi <- clamp(rnorm(n_d, 0.50, 0.06), 0.36, 0.70)
  gvi0 <- clamp(rnorm(n_d, 15, 4), 5, 28)
  pop_d <- (P * 1000 / n_d) * clamp(rnorm(n_d, 1, 0.25), 0.4, 2)
  a_u <- jit(2.6, 0.5, 1, 4); su <- jit(0.0, 3.0)          # uniform slope ~ 0
  a_e <- jit(2.6, 0.5, 1, 4); se <- jit(4.0, 3.0)          # equity slope > 0
  dgvi_u <- pmax(0, a_u + su * (svi - 0.5) + rnorm(n_d, 0, 0.6))
  dgvi_e <- pmax(0, a_e + se * (svi - 0.5) + rnorm(n_d, 0, 0.6))
  comp_df <- data.frame(
    region_id = seq_len(n_d),
    region_name = sprintf("District %d", seq_len(n_d)),
    GVI_baseline = gvi0, SVI = svi, population = pop_d)
  comp_df[["ΔGVI_uniform"]] <- dgvi_u
  comp_df[["ΔGVI_equity"]] <- dgvi_e
  comp_df$GVI_after_uniform <- gvi0 + dgvi_u
  comp_df$GVI_after_equity <- gvi0 + dgvi_e
  comp_df[["Δallocation"]] <- dgvi_e - dgvi_u
  readr::write_csv(comp_df, file.path(tdir, sprintf("%s_policy_comparison.csv", city)))

  # --- cost-benefit frontier (budget sweep) ----------------------------------
  total_cost_max <- trees_pv + ac_pv + ews_pv
  ben_max <- av_trees + av_ac_net + av_ews
  bfrac <- seq(0.35, 1.0, length.out = 8)
  max_cost <- bfrac * total_cost_max * jit(1, 0.0)          # ~= budget
  max_benefit <- ben_max * (bfrac^0.8) * clamp(rnorm(8, 1, 0.02), 0.9, 1.1)
  bud_df <- data.frame(
    budget = bfrac * total_cost_max,
    max_ben_trees = clamp(bfrac + rnorm(8, 0, .05), 0, 1),
    max_ben_ac = clamp(bfrac * 0.9 + rnorm(8, 0, .05), 0, 1),
    max_ben_ews = 1,
    max_benefit = max_benefit,
    max_cost = max_cost,
    best_ce_trees = 0, best_ce_ac = 0, best_ce_ews = 1,
    best_ce_benefit = max_benefit * 0.55,
    best_ce_cost = max_cost * 0.1)
  readr::write_csv(bud_df, file.path(tdir, sprintf("%s_budget_sensitivity.csv", city)))

  # --- constrained portfolios (points near the frontier top) -----------------
  con_df <- data.frame(
    cost = total_cost_max * c(1.00, 1.00, 0.83, 0.83),
    benefit = ben_max * c(1.00, 1.00, 0.92, 0.92),
    trees = c(1, 1, 1, 1), ac = c(1, 1, 0.8, 0.8), ews = c(1, 1, 1, 1),
    scenario = c("Unconstrained", "Min 20% Trees", "Max 80% AC", "Balanced"))
  readr::write_csv(con_df, file.path(tdir, sprintf("%s_policy_constraints.csv", city)))

  # --- CBA summary JSON (fields the figures read, plus structure) ------------
  waste_pen <- av_ac_gross - av_ac_net                       # standalone AC penalty
  pen_with_trees <- waste_pen * jit(0.88, 0.03, 0.75, 0.98)  # greening softens it
  on_top_ac <- av_trees * jit(0.95, 0.02, 0.82, 1.0)         # tree benefit on top of AC
  tree_cov <- jit(0.9 + 0.12 * (W - 16), 0.25, 0.3, 3.6)     # elec saving, % of tree cost
  pv_elec_saving <- tree_cov / 100 * trees_pv                # PV euro of AC elec saved
  kwh_25y <- pv_elec_saving / 0.25                           # ~euro -> kWh
  co2_t <- kwh_25y * 180 / 1e6                               # tonnes CO2 (180 gCO2/kWh)
  cba <- list(
    costs = list(
      trees = list(pv_total_base = trees_pv),
      ac = list(pv_total = ac_pv, pv_elec_veg_saving = pv_elec_saving),
      ews = list(pv_total = ews_pv)),
    benefits = list(
      trees = list(avoided_deaths_25y = av_trees, on_top_of_ac_25y = on_top_ac),
      ac = list(gross_25y = av_ac_gross, net_25y = av_ac_net,
                waste_heat_penalty_25y = waste_pen),
      ac_with_trees_interaction = list(
        gross_25y = av_ac_gross, net_25y = av_ac_gross - pen_with_trees,
        waste_heat_penalty_25y = pen_with_trees),
      ews = list(avoided_deaths_25y = av_ews)),
    cost_effectiveness = list(
      trees_base = trees_pv / av_trees,
      ac_net = ac_pv / av_ac_net,
      ews = ews_pv / av_ews),
    vegetation_feedbacks = list(
      falchetta_electricity = list(
        enabled = TRUE,
        pv_tree_elec_saving_all_policy_ac_users = pv_elec_saving,
        cum_co2_avoided_all_policy_ac_users_t_25y = co2_t,
        co2_intensity_gCO2_per_kwh = 180,
        tree_cost_coverage_current_ac_users_pct = tree_cov * 0.86,
        tree_cost_coverage_all_policy_ac_users_pct = tree_cov),
      lambda_y_waste_heat = list(
        penalty_standalone_25y = waste_pen,
        penalty_with_trees_25y = pen_with_trees)),
    parameters = list(discount_rate = 0.03, horizon_years = 25,
                      tree_maturity_years = 12, tree_start_age = 5,
                      waste_heat_case = "central", waste_heat_enabled = TRUE))
  write_json(cba, file.path(tdir, sprintf("%s_cba_summary.json", city)),
             auto_unbox = TRUE, pretty = TRUE, digits = 8)

  # --- SI-support files (for planning future SI figures) ---------------------
  disc <- c(0.01, 0.03, 0.05)
  scale_d <- (0.03 / disc)^0.9
  disc_df <- data.frame(
    discount_rate = disc,
    trees_pv = trees_pv * scale_d, ac_pv = ac_pv * scale_d, ews_pv = ews_pv * scale_d,
    trees_per_death = trees_pv * scale_d / av_trees,
    ac_per_death = ac_pv * scale_d / av_ac_net,
    ews_per_death = ews_pv * scale_d / av_ews)
  readr::write_csv(disc_df, file.path(tdir, sprintf("%s_discount_sensitivity.csv", city)))

  age_df <- data.frame(
    tree_age = c(5, 0),
    pv_om = c(trees_pv * 0.45, trees_pv * 0.31),
    pv_total = c(trees_pv, trees_pv * 0.86),
    avoided_deaths = c(av_trees, av_trees * 0.69),
    cost_per_death = c(trees_pv / av_trees, trees_pv * 0.86 / (av_trees * 0.69)))
  readr::write_csv(age_df, file.path(tdir, sprintf("%s_tree_age_sensitivity.csv", city)))

  wd_df <- data.frame(
    year = YEARS, n_warning_days = nwd_series,
    threshold_deaths_per_day = jit(6.7, 0.5, 4, 9),
    threshold_method = "epi_deaths_file", warning_fallback_used = "False",
    season_start_md = "05-01", season_end_md = "09-30", season_source = "warning_season")
  readr::write_csv(wd_df, file.path(tdir, sprintf("%s_ews_warning_days.csv", city)))

  invisible(TRUE)
}

# ---- copy real cities verbatim ---------------------------------------------
copy_real_city <- function(city) {
  src_base <- file.path(REPO_ROOT, "urban-heat", "outputs_variants",
                        "masselot_main_agnostic", city)
  if (!dir.exists(src_base)) {
    message(sprintf("  [note] real %s not found; generating it synthetically instead", city))
    return(FALSE)
  }
  dst_tdir <- city_path(city, where = "tables")
  dir.create(dst_tdir, showWarnings = FALSE, recursive = TRUE)
  # tables/ is all small CSV/JSON -> copy wholesale; plus the root summary CSV.
  file.copy(list.files(file.path(src_base, "tables"), full.names = TRUE),
            dst_tdir, overwrite = TRUE)
  ad <- file.path(src_base, sprintf("%s_avoided_deaths_summary.csv", city))
  if (file.exists(ad)) file.copy(ad, city_path(city, where = "root"), overwrite = TRUE)
  TRUE
}

# ---- metadata (climate metrics + clusters) ---------------------------------
# Uses real cached heat metrics for the real cities where available; profile
# values for everyone else. Clusters all cities together via assign_clusters().
build_synth_meta <- function(copied_real) {
  real_cache <- file.path(VIS_ROOT, "tables", ".city_heat_cache.csv")
  rc <- if (file.exists(real_cache))
    suppressWarnings(readr::read_csv(real_cache, show_col_types = FALSE)) else NULL

  heat <- do.call(rbind, lapply(seq_len(nrow(PROFILES)), function(i) {
    r <- PROFILES[i, ]
    use_real <- r$city %in% copied_real && !is.null(rc) && r$city %in% rc$city
    if (use_real) {
      cr <- rc[rc$city == r$city, ][1, ]
      data.frame(city = r$city, warmseason_mean_t2m = cr$warmseason_mean_t2m,
                 t2m_p95 = cr$t2m_p95, hot_days = cr$hot_days)
    } else {
      set.seed(city_seed(r$city) + 1L)
      data.frame(city = r$city, warmseason_mean_t2m = r$W,
                 t2m_p95 = round(r$W + jit(3.2, 0.4), 3), hot_days = r$H)
    }
  }))

  meta <- merge(PROFILES[, c("city", "iso3", "country", "pop_k")], heat, by = "city")
  meta$capital <- FALSE
  meta <- assign_clusters(meta)
  meta$city_name <- city_label(meta$city)
  meta$city_label <- city_label(meta$city)
  meta <- meta[order(meta$warmseason_mean_t2m), ]
  # column order matching the real city_metadata.csv
  cols <- c("city", "city_name", "iso3", "country", "pop_k", "capital",
            "warmseason_mean_t2m", "t2m_p95", "hot_days", "climate_cluster",
            "cluster_provisional", "is_exemplar", "city_label")
  meta[, intersect(cols, names(meta))]
}

# ---- driver -----------------------------------------------------------------
generate_synthetic <- function() {
  banner(sprintf("Synthetic city generator -> %s", basename(OUTPUTS_BASE)))
  if (identical(basename(OUTPUTS_BASE), "masselot_main_agnostic"))
    stop("Refusing to write synthetic data into the REAL variant dir. ",
         "Set NATCITIES_VARIANT to a synthetic name first.")
  dir.create(OUTPUTS_BASE, showWarnings = FALSE, recursive = TRUE)

  copied_real <- character(0)
  for (city in REAL_CITIES) {
    if (copy_real_city(city)) {
      copied_real <- c(copied_real, city)
      message(sprintf("  [real] copied %s", city))
    }
  }
  synth <- setdiff(PROFILES$city, copied_real)
  for (city in synth) {
    gen_city_files(PROFILES[PROFILES$city == city, ])
    writeLines(sprintf("synthetic placeholder generated %s", city),
               city_path(city, ".SYNTHETIC", where = "root"))
  }
  message(sprintf("  [synth] generated %d cities", length(synth)))

  meta <- build_synth_meta(copied_real)
  # back up any existing metadata in this (synthetic) tables dir
  mp <- file.path(TAB_DIR, "city_metadata.csv")
  dir.create(TAB_DIR, showWarnings = FALSE, recursive = TRUE)
  readr::write_csv(meta, mp)
  message(sprintf("  [saved] %s (%d cities)", mp, nrow(meta)))

  manifest <- data.frame(
    city = PROFILES$city,
    kind = ifelse(PROFILES$city %in% copied_real, "real-copy", "synthetic"),
    pop_k = PROFILES$pop_k)
  readr::write_csv(manifest, file.path(TAB_DIR, ".synthetic_manifest.csv"))
  print(meta[, c("city_label", "country", "pop_k", "warmseason_mean_t2m",
                 "hot_days", "climate_cluster", "is_exemplar")], row.names = FALSE)
  invisible(meta)
}

if (!exists(".NATCITIES_NORUN")) generate_synthetic()
