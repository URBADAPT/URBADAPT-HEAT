# =============================================================================
# si1_sensitivity.R  --  SI: how the cost-effectiveness ranking holds up
# Every panel asks whether an assumption could reorder the three levers.
#
#   (a) Discount rate (1-7%): cost per death by pathway.
#   (b) Tree age at planting (0-15y): planting older stock buys effect sooner
#       but costs more -- does it change trees' cost per death?
#   (c) Greening ambition (target GVI): does more greening get cheaper or
#       dearer per life saved?
#
# Sources: <city>_discount_sensitivity.csv, <city>_tree_age_sensitivity.csv,
# interim/<city>_trees_target_sensitivity.csv (all 40 cities).
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

# Median with an interquartile ribbon across cities: 40 spaghetti lines would be
# unreadable, and the spread is the point, not any individual city.
band <- function(df, x, y, group = NULL) {
  gcols <- c(x, group)
  df |>
    dplyr::filter(is.finite(.data[[y]]), .data[[y]] > 0) |>
    dplyr::group_by(dplyr::across(dplyr::all_of(gcols))) |>
    dplyr::summarise(n = dplyr::n(),
                     med = median(.data[[y]]),
                     lo  = quantile(.data[[y]], 0.25),
                     hi  = quantile(.data[[y]], 0.75), .groups = "drop")
}

build_si1 <- function(cities = discover_cities()) {
  banner("SI 1: discount rate, tree age and greening ambition")
  meta <- load_city_meta()
  if (!length(cities)) { message("No cities."); return(invisible(NULL)) }

  # --- (a) discount rate -----------------------------------------------------
  ds <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_discount_sensitivity.csv", c), quiet = TRUE)
    need <- c("discount_rate", "trees_per_death", "ac_per_death", "ews_per_death")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d[, need]
  })
  pa <- if (!is.null(ds) && nrow(ds)) {
    l <- tidyr::pivot_longer(ds, c("trees_per_death", "ac_per_death", "ews_per_death"),
                             names_to = "lever", values_to = "cpd")
    l$pathway <- factor(dplyr::recode(l$lever, trees_per_death = "Trees",
                          ac_per_death = "AC", ews_per_death = "EWS"),
                        levels = PATHWAY_LEVELS)
    b <- band(l, "discount_rate", "cpd", "pathway")
    ggplot(b, aes(discount_rate, med, color = pathway, fill = pathway)) +
      geom_ribbon(aes(ymin = lo, ymax = hi), alpha = 0.16, color = NA) +
      geom_line(linewidth = 0.9) + geom_point(size = 1.5) +
      scale_color_manual(values = PATHWAY_COLORS, name = "Lever") +
      scale_fill_manual(values = PATHWAY_COLORS, guide = "none") +
      scale_x_continuous(labels = scales::label_percent(accuracy = 1)) +
      eur_log_scale("Cost per death avoided (€, log)") +
      labs(tag = "a", title = "Discount rate",
           subtitle = "Median across cities, IQR shaded; the ranking never crosses",
           x = "Discount rate") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (b) what the greening cost assumptions are worth ----------------------
  # Two assumptions move the tree cost per death: the age of the planted stock
  # (only two levels are modelled, 0 and 5 years) and the maintenance intensity
  # (base vs 5x O&M). They come from different files but belong on one axis --
  # both answer "how much does the greening cost assumption matter?".
  TREE_ASSUMPTIONS <- c("Planted at age 0", "Planted at age 5\n(base)", "5x maintenance")
  ta <- gather_cities(cities, function(c) {
    age <- read_city_csv(c, sprintf("%s_tree_age_sensitivity.csv", c), quiet = TRUE)
    cea <- read_cea(c, headline_only = FALSE)
    out <- list()
    if (!is.null(age) && all(c("tree_age", "cost_per_death", "avoided_deaths") %in% names(age))) {
      # Same censoring as read_cea(): Madrid and Sevilla greening avoid
      # essentially nothing, so their cost per death is a ~1e10 artefact rather
      # than a real outlier, and would set the axis for all 40 cities.
      age <- age[as.numeric(age$avoided_deaths) >= NO_BENEFIT_DEATHS, , drop = FALSE]
      if (nrow(age)) out$age <- data.frame(
        assumption = ifelse(age$tree_age == 0, TREE_ASSUMPTIONS[1], TREE_ASSUMPTIONS[2]),
        cost_per_death = as.numeric(age$cost_per_death))
    }
    if (!is.null(cea))
      out$om <- data.frame(assumption = TREE_ASSUMPTIONS[3],
        cost_per_death = cea$cost_per_death[cea$Policy == "Trees (5x O&M)"])
    if (!length(out)) return(NULL)
    dplyr::bind_rows(out)
  })
  pb <- if (!is.null(ta) && nrow(ta)) {
    ta <- attach_meta(ta, meta)
    ta$assumption <- factor(ta$assumption, levels = TREE_ASSUMPTIONS)
    d <- ta[is.finite(ta$cost_per_death) & ta$cost_per_death > 0, , drop = FALSE]
    ggplot(d, aes(assumption, cost_per_death)) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95", color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.13, height = 0,
                  size = 1.8, alpha = 0.75) +
      cluster_scale() +
      eur_log_scale("Cost per death avoided (€, log)") +
      labs(tag = "b", title = "Greening cost assumptions",
           subtitle = "Maintenance intensity matters far more than planting age",
           x = NULL) +
      theme_natcities()
  } else patchwork::plot_spacer()

  # --- (c) greening ambition -------------------------------------------------
  ts <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_trees_target_sensitivity.csv", c),
                       where = "interim", quiet = TRUE)
    need <- c("scenario", "avoided_deaths_25y", "cost_per_death")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d <- d[as.numeric(d$avoided_deaths_25y) >= NO_BENEFIT_DEATHS, need, drop = FALSE]
    if (!nrow(d)) NULL else d
  })
  pc <- if (!is.null(ts) && nrow(ts)) {
    ts <- attach_meta(ts, meta)
    lev <- c("Q3 (baseline)", "Q3 + 5", "Q3 + 10", "Max observed")
    ts$scenario <- factor(ts$scenario, levels = intersect(lev, unique(ts$scenario)))
    d <- ts[is.finite(ts$cost_per_death) & ts$cost_per_death > 0, , drop = FALSE]
    ggplot(d, aes(scenario, cost_per_death)) +
      geom_boxplot(outlier.shape = NA, width = 0.55, fill = "grey95", color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.13, height = 0,
                  size = 1.8, alpha = 0.75) +
      cluster_scale() +
      eur_log_scale("Cost per death avoided (€, log)") +
      labs(tag = "c", title = "Greening ambition",
           subtitle = "Target canopy vs the city's 3rd-quartile district;\nplanting more does not get cheaper per life saved",
           x = NULL) +
      theme_natcities() +
      theme(axis.text.x = element_text(angle = 20, hjust = 1))
  } else patchwork::plot_spacer()

  fig <- (pa | pb | pc) &
    theme(legend.position = "bottom",
          plot.subtitle = element_text(size = rel(0.8)))
  save_item(fig, "si1_sensitivity", width = 14, height = 5.2)
}

if (!exists(".NATCITIES_NORUN")) build_si1()
