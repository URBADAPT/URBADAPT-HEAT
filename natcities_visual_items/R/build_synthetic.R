# =============================================================================
# build_synthetic.R  --  render every visual item against the SYNTHETIC 40-city
# dataset, for planning figure revisions at full scale. Fully isolated from the
# real pipeline: reads a synthetic variant dir and writes to figures_synthetic/
# + tables_synthetic/. Run under PowerShell:
#   cd natcities_visual_items/R
#   Rscript build_synthetic.R
# Nothing here touches the real variant, figures/ or tables/. Undo with
# clean_synthetic.R.
# =============================================================================

.d <- {
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f)) else getwd()
}

# Point the pipeline at the synthetic variant + separate output dirs BEFORE
# sourcing _helpers.R (paths are resolved at source time).
Sys.setenv(NATCITIES_VARIANT = Sys.getenv("NATCITIES_VARIANT",
                                           "masselot_main_agnostic_synthetic"))
VIS <- normalizePath(file.path(.d, ".."), winslash = "/")
Sys.setenv(NATCITIES_FIG_DIR = file.path(VIS, "figures_synthetic"))
Sys.setenv(NATCITIES_TAB_DIR = file.path(VIS, "tables_synthetic"))

.NATCITIES_NORUN <- TRUE
source(file.path(.d, "_helpers.R"))

# 1. Generate the synthetic dataset + metadata (skips real Milan/Rome, copies them).
source(file.path(.d, "gen_synthetic_cities.R"))
tryCatch(generate_synthetic(),
         error = function(e) message("  [FAILED] generate: ", conditionMessage(e)))

cities <- discover_cities()
banner(sprintf("Synthetic cities: %d (%s ...)", length(cities),
               paste(head(cities, 6), collapse = ", ")))

# 2. Figures + tables (same builders as the real pipeline; no 00_city_meta run).
for (s in c("fig1_risk_effectiveness.R", "fig2_distribution.R", "fig3_synergies.R",
            "exemplars.R", "tab_cba_by_city.R", "tab_risk_by_city.R")) {
  source(file.path(.d, s))
}
steps <- list(
  function() build_fig1(cities),
  function() build_fig2(cities),
  function() build_fig3(cities),
  function() build_exemplars(),
  function() build_tab_cba(cities),
  function() build_tab_risk(discover_cities(require_tables = FALSE)))
for (st in steps) tryCatch(st(), error = function(e) message("  [FAILED] ", conditionMessage(e)))

banner("Done. Synthetic outputs in figures_synthetic/ and tables_synthetic/")
