# =============================================================================
# tab_ews_by_city.R  --  the early-warning pathway, city by city
# EWS is the cheapest lever in every main figure, so it gets its own table.
# Columns: City, Cluster, Warning days 2020 / 2050, Deaths on warning days (%),
#          Avoided deaths (25y), Avoided / 100k, PV cost (k€), Cost per death (k€),
#          Attribution (which EWS wording that city's run produced).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

build_tab_ews <- function(cities = discover_cities()) {
  banner("Table: early-warning pathway by city")
  meta <- load_city_meta()
  if (!length(cities)) { message("No cities."); return(invisible(NULL)) }

  dat <- gather_cities(cities, function(c) {
    eff <- canonical_effectiveness(c)
    eff <- if (is.null(eff)) NULL else eff[eff$pathway == "EWS", , drop = FALSE]
    if (is.null(eff) || !nrow(eff)) return(NULL)
    wd  <- read_city_csv(c, sprintf("%s_ews_warning_days.csv", c), quiet = TRUE)
    base <- read_city_csv(c, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", c),
                          quiet = TRUE)
    cost <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c), quiet = TRUE)
    at_year <- function(d, col, yr) {
      if (is.null(d) || !all(c("year", col) %in% names(d))) return(NA_real_)
      v <- d[[col]][d$year == yr]
      if (!length(v)) NA_real_ else as.numeric(v[1])
    }
    ews_pv <- if (!is.null(cost)) {
      r <- cost$total_pv[pathway_key(cost$policy) == "EWS"]
      if (length(r)) as.numeric(r[1]) else NA_real_
    } else NA_real_
    data.frame(
      variant    = eff$policy_variant[1],
      avoided    = eff$avoided_deaths_25y[1],
      pct_red    = eff$pct_reduction[1],
      wd_2020    = at_year(wd, "n_warning_days", 2020),
      wd_2050    = at_year(wd, "n_warning_days", 2050),
      frac_2020  = at_year(base, "frac_deaths_on_warning_days", 2020),
      ews_pv     = ews_pv)
  })
  if (is.null(dat)) { message("No EWS data."); return(invisible(NULL)) }
  dat <- attach_meta(dat, meta)
  dat$avoided_100k   <- per_100k(dat$avoided, dat$pop_k)
  dat$cost_per_death <- ifelse(dat$avoided >= NO_BENEFIT_DEATHS,
                               dat$ews_pv / dat$avoided, NA_real_)
  dat <- dat[order(dat$climate_cluster, -dat$avoided_100k), ]

  out <- data.frame(
    City = dat$city_label,
    Cluster = as.character(dat$climate_cluster),
    `Warning days 2020` = round(dat$wd_2020, 0),
    `Warning days 2050` = round(dat$wd_2050, 0),
    `Deaths on warning days (%)` = round(100 * dat$frac_2020, 0),
    `Avoided deaths (25y)` = round(dat$avoided, 0),
    `Avoided / 100k` = round(dat$avoided_100k, 1),
    `PV cost (k€)` = round(dat$ews_pv / 1e3, 0),
    `Cost per death (k€)` = round(dat$cost_per_death / 1e3, 0),
    Attribution = dat$variant,
    check.names = FALSE)

  save_table(out, "tab_ews_by_city",
             caption = paste("Early-warning pathway by city. `Attribution' records",
               "which wording the city's run produced for the EWS row of",
               "\\texttt{policy\\_effectiveness}; all three carry the same central",
               "estimate as the city's \\texttt{EWS (central)} cost-effectiveness row."),
             label = "tab:ews_by_city",
             align = c("l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "l"),
             digits = c(0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0))
  message(sprintf("  %d cities; attribution wording: %s", nrow(out),
                  paste(names(table(out$Attribution)), table(out$Attribution),
                        sep = "=", collapse = ", ")))
  invisible(out)
}

if (!exists(".NATCITIES_NORUN")) build_tab_ews()
