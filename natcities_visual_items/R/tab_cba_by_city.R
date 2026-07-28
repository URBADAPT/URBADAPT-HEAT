# =============================================================================
# tab_cba_by_city.R  --  big summary table: cost-benefit by city x pathway
# All cities, city-specific rows (this absorbs the detail kept out of figures).
# Columns: City, Cluster, Pathway, PV cost (M€), Avoided deaths (25y),
#          Cost per death (k€), Public share (%). CSV + \input-able LaTeX.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_tab_cba <- function(cities = discover_cities()) {
  banner("Table: cost-benefit by city x pathway")
  meta <- load_city_meta()
  if (!length(cities)) { message("No cities."); return(invisible(NULL)) }

  keep <- c("Trees (uniform)", "AC (NET)", "EWS (marginal)")
  dat <- gather_cities(cities, function(c) {
    costs <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c))
    eff   <- read_city_csv(c, sprintf("%s_policy_effectiveness.csv", c))
    if (is.null(costs) || is.null(eff)) return(NULL)
    costs$pathway <- pathway_key(costs$policy)
    eff <- eff[eff$policy %in% keep, ]; eff$pathway <- pathway_key(eff$policy)
    m <- merge(costs[, c("pathway", "total_pv", "public_share")],
               eff[, c("pathway", "avoided_deaths_25y")], by = "pathway")
    m$cost_per_death <- m$total_pv / m$avoided_deaths_25y
    m
  })
  if (is.null(dat)) { message("Insufficient data."); return(invisible(NULL)) }
  dat <- attach_meta(dat, meta)
  dat$pathway <- factor(dat$pathway, levels = c("Trees", "AC", "EWS"))
  dat <- dat[order(dat$climate_cluster, dat$city_label, dat$pathway), ]

  out <- data.frame(
    City                 = dat$city_label,
    Cluster              = as.character(dat$climate_cluster),
    Pathway              = as.character(dat$pathway),
    `PV cost (M€)`       = round(dat$total_pv / 1e6, 1),
    `Avoided deaths (25y)` = round(dat$avoided_deaths_25y, 0),
    `Cost per death (k€)`  = round(dat$cost_per_death / 1e3, 0),
    `Public share (%)`     = round(dat$public_share * 100, 0),
    check.names = FALSE)

  save_table(out, "tab_cba_by_city",
             caption = "Cost-benefit of adaptation pathways by city and climate cluster.",
             label = "tab:cba_by_city",
             align = c("l", "l", "l", "l", "r", "r", "r", "r"),
             digits = c(0, 0, 0, 0, 1, 0, 0, 0))
  print(out, row.names = FALSE)
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_cba()
