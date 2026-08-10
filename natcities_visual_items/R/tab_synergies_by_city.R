# =============================================================================
# tab_synergies_by_city.R  --  the lever interactions behind Fig 4, city by city
# Columns: City, Cluster, ΔGVI, AC gross / net avoided deaths (25y),
#          waste-heat penalty (deaths and % of gross), penalty removed by
#          greening (%), greening benefit surviving alongside AC (%).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_tab_synergies <- function(cities = discover_cities()) {
  banner("Table: lever interactions by city")
  meta <- load_city_meta()
  if (!length(cities)) { message("No cities."); return(invisible(NULL)) }

  syn <- gather_cities(cities, read_cba)
  if (is.null(syn)) { message("No CBA summaries."); return(invisible(NULL)) }
  syn <- attach_meta(syn, meta)

  syn$penalty_pct <- ifelse(syn$ac_gross > NO_BENEFIT_DEATHS,
                            100 * syn$ac_penalty / syn$ac_gross, NA_real_)
  syn$penalty_removed_pct <- ifelse(syn$ac_penalty > 1e-6,
                            100 * (1 - syn$penalty_trees / syn$ac_penalty), NA_real_)
  syn$surviving_pct <- ifelse(syn$trees_alone > NO_BENEFIT_DEATHS,
                            100 * syn$trees_ontop / syn$trees_alone, NA_real_)
  syn <- syn[order(syn$climate_cluster, -syn$penalty_pct), ]

  out <- data.frame(
    City = syn$city_label,
    Cluster = as.character(syn$climate_cluster),
    `ΔGVI (points)` = round(syn$delta_gvi, 1),
    `AC gross (25y)` = round(syn$ac_gross, 0),
    `AC net (25y)` = round(syn$ac_net, 0),
    `Waste-heat penalty (deaths)` = round(syn$ac_penalty, 1),
    `Penalty (% of gross)` = round(syn$penalty_pct, 1),
    `Penalty removed by trees (%)` = round(syn$penalty_removed_pct, 1),
    `Greening benefit surviving AC (%)` = round(syn$surviving_pct, 1),
    check.names = FALSE)

  save_table(out, "tab_synergies_by_city",
             caption = paste("Interaction between private cooling and public",
               "greening by city. The waste-heat penalty is the share of AC's",
               "gross mortality benefit taken back by the warming its own",
               "operation adds; the last column is greening's benefit alongside",
               "AC as a share of its stand-alone benefit."),
             label = "tab:synergies_by_city",
             align = c("l", "l", "l", "r", "r", "r", "r", "r", "r", "r"),
             digits = c(0, 0, 0, 1, 0, 0, 1, 1, 1, 1))
  message(sprintf("  %d cities", nrow(out)))
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_synergies()
