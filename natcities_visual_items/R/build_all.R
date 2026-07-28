# =============================================================================
# build_all.R  --  render every Nature Cities visual item
# Usage (run under PowerShell -- ncdf4 segfaults under Git Bash Rscript here):
#   Rscript build_all.R
#
# Order: metadata (climate metrics + clusters) -> summary figures -> exemplar
# dashboards -> big city tables. Auto-discovers cities; missing inputs skip.
# =============================================================================

.d <- {
  a <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", a[grep("^--file=", a)])
  if (length(f)) dirname(normalizePath(f)) else getwd()
}
.NATCITIES_NORUN <- TRUE
source(file.path(.d, "_helpers.R"))

# 1. Metadata (also (re)clusters). Reads the heat cache; recomputes only new cities.
source(file.path(.d, "00_city_meta.R"))
tryCatch(build_city_meta(), error = function(e) message("  [FAILED] meta: ", conditionMessage(e)))

cities <- discover_cities()
banner(sprintf("Cities with results: %s", paste(cities, collapse = ", ")))

# 2. Figures + tables
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
  function() build_tab_risk())
for (st in steps) tryCatch(st(), error = function(e) message("  [FAILED] ", conditionMessage(e)))

banner("Done. Outputs in natcities_visual_items/figures and /tables")
