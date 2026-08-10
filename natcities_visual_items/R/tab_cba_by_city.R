# =============================================================================
# tab_cba_by_city.R  --  big summary table: cost-benefit by city x pathway
# All cities, city-specific rows (this absorbs the detail kept out of figures).
# Columns: City, Cluster, Pathway, PV cost (M€), PV cost per capita (€),
#          Avoided deaths (25y), Cost per death (k€), Public share (%).
# CSV + \input-able LaTeX.
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

  dat <- gather_cities(cities, function(c) {
    costs <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c))
    eff   <- canonical_effectiveness(c)
    if (is.null(costs) || is.null(eff)) return(NULL)
    costs$pathway <- pathway_key(costs$policy)
    m <- merge(costs[, c("pathway", "total_pv", "public_share")],
               eff[, c("pathway", "policy_variant", "avoided_deaths_25y")],
               by = "pathway")
    m$cost_per_death <- m$total_pv / m$avoided_deaths_25y
    # A pathway that avoids essentially no deaths gives Inf or a meaningless
    # multi-billion euro figure (Madrid and Sevilla greening); report both the
    # ratio and the deaths as missing rather than printing the artefact.
    bad <- !is.finite(m$cost_per_death) | m$avoided_deaths_25y < NO_BENEFIT_DEATHS
    m$cost_per_death[bad] <- NA_real_
    m$no_benefit <- bad
    m
  })
  if (is.null(dat)) { message("Insufficient data."); return(invisible(NULL)) }
  dat <- attach_meta(dat, meta)
  dat$pathway <- factor(dat$pathway, levels = PATHWAY_LEVELS)
  dat <- dat[order(dat$climate_cluster, dat$city_label, dat$pathway), ]

  n_flag <- sum(dat$no_benefit, na.rm = TRUE)
  if (n_flag) message(sprintf(
    "  [note] %d city-pathway row(s) avoid < %.1f deaths over 25y; cost per death left blank: %s",
    n_flag, NO_BENEFIT_DEATHS,
    paste(sprintf("%s/%s", dat$city_label[dat$no_benefit],
                  dat$pathway[dat$no_benefit]), collapse = ", ")))

  out <- data.frame(
    City                 = dat$city_label,
    Cluster              = as.character(dat$climate_cluster),
    Pathway              = as.character(dat$pathway),
    `PV cost (M€)`       = round(dat$total_pv / 1e6, 1),
    `PV cost per capita (€)` = round(dat$total_pv / (dat$pop_k * 1000), 0),
    `Avoided deaths (25y)` = round(dat$avoided_deaths_25y, 0),
    `Cost per death (k€)`  = round(dat$cost_per_death / 1e3, 0),
    `Public share (%)`     = round(dat$public_share * 100, 0),
    check.names = FALSE)

  save_table(out, "tab_cba_by_city",
             caption = paste("Cost-benefit of adaptation pathways by city and",
               "climate cluster. AC is reported net of its waste-heat feedback.",
               "A blank cost per death marks a pathway avoiding fewer than",
               "0.5 deaths over the 25-year horizon."),
             label = "tab:cba_by_city",
             align = c("l", "l", "l", "l", "r", "r", "r", "r", "r"),
             digits = c(0, 0, 0, 0, 1, 0, 0, 0, 0))
  message(sprintf("  %d rows, %d cities, pathways: %s", nrow(out),
                  length(unique(out$City)),
                  paste(names(table(out$Pathway)), table(out$Pathway),
                        sep = "=", collapse = " ")))
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_cba()
