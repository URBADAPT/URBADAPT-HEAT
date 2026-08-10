# =============================================================================
# build_all.R  --  render every Nature Cities visual item
# Usage (run under PowerShell -- ncdf4 segfaults under Git Bash Rscript here):
#   $env:NATCITIES_OUTPUTS_BASE = "<path to the 40-city results tree>"
#   Rscript build_all.R
#
# Order: metadata (climate metrics + clusters) -> main figures -> SI figures ->
# exemplar dashboards -> big city tables. Auto-discovers cities; missing inputs
# skip rather than abort.
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
banner(sprintf("Cities with results (%d): %s", length(cities),
               paste(cities, collapse = ", ")))

# 2. Figures + tables
SCRIPTS <- c("fig1_risk_effectiveness.R", "fig2_distribution.R",
             "fig3_costeffectiveness.R", "fig4_synergies.R",
             "si1_sensitivity.R", "si2_if_comparison.R", "si3_ews.R",
             "exemplars.R", "tab_cba_by_city.R", "tab_risk_by_city.R",
             "tab_ews_by_city.R", "tab_synergies_by_city.R", "make_report.R")
for (s in SCRIPTS) source(file.path(.d, s))

steps <- list(
  fig1 = function() build_fig1(cities),
  fig2 = function() build_fig2(cities),
  fig3 = function() build_fig3(cities),
  fig4 = function() build_fig4(cities),
  si1  = function() build_si1(cities),
  si2  = function() build_si2(cities),
  si3  = function() build_si3(cities),
  exemplars = function() build_exemplars(),
  tab_cba   = function() build_tab_cba(cities),
  tab_risk  = function() build_tab_risk(),
  tab_ews   = function() build_tab_ews(cities),
  tab_syn   = function() build_tab_synergies(cities),
  # Last: the report indexes whatever the steps above just wrote.
  report    = function() build_report())

failed <- character(0)
for (nm in names(steps)) {
  ok <- tryCatch({ steps[[nm]](); TRUE },
                 error = function(e) { message("  [FAILED] ", nm, ": ",
                                               conditionMessage(e)); FALSE })
  if (!ok) failed <- c(failed, nm)
}

banner(if (length(failed))
         sprintf("Done with %d failure(s): %s", length(failed),
                 paste(failed, collapse = ", "))
       else "Done. Outputs in natcities_visual_items/figures and /tables")
