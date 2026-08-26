# =============================================================================
# si2_if_comparison.R  --  SI: sensitivity to the exposure-response function
# The mortality results rest on one choice: which temperature-mortality curve
# translates degrees into deaths. The runs ship four, so the SI shows what that
# choice is worth.
#
#   (a) The curves themselves: marginal deaths per degree against temperature,
#       by age group, median across cities with an IQR ribbon.
#   (b) What the choice does to the headline: baseline heat deaths under each
#       family, relative to the Masselot main specification.
#
# Sources: <city>_masselot_main_burke_sensitivity_if_comparison.csv (39 cities)
# and interim/annual_heat_deaths_generic_<city>[_<family>].csv.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

IF_LABELS <- c(masselot         = "Masselot (main)",
               masselot_tail    = "Masselot, tail-extended",
               burke_polynomial = "Burke, polynomial",
               burke_powerlaw   = "Burke, power law")
IF_COLORS <- c("Masselot (main)"          = "#1565C0",
               "Masselot, tail-extended"  = "#00838F",
               "Burke, polynomial"        = "#E65100",
               "Burke, power law"         = "#B71C1C")

# Per-family baseline mortality, summed over the three age-group columns
# (`<15`, `15-64`, `65+`) that these files carry instead of a total column.
# The plain unsuffixed file duplicates the main Masselot specification.
AGE_COLS <- c("<15", "15-64", "65+")

if_total_deaths <- function(city, family) {
  d <- read_city_csv(city, sprintf("annual_heat_deaths_generic_%s_%s.csv", city, family),
                     where = "interim", quiet = TRUE)
  if (is.null(d)) d <- read_city_csv(city, sprintf("annual_heat_deaths_generic_%s.csv", city),
                                     where = "interim", quiet = TRUE)
  cols <- intersect(AGE_COLS, names(d))
  if (is.null(d) || !length(cols)) return(NA_real_)
  sum(as.matrix(d[, cols]), na.rm = TRUE)
}

build_si2 <- function(cities = discover_cities()) {
  banner("SI 2: exposure-response function sensitivity")
  meta <- load_city_meta()
  if (!length(cities)) { message("No cities."); return(invisible(NULL)) }

  # --- (a) the four curves ---------------------------------------------------
  ifc <- gather_cities(cities, function(c) {
    d <- read_city_csv(c, sprintf("%s_masselot_main_burke_sensitivity_if_comparison.csv", c),
                       quiet = TRUE)
    need <- c("if_family", "age_group", "temperature_C", "mdd")
    if (is.null(d) || !all(need %in% names(d))) return(NULL)
    d[, need]
  })
  pa <- if (!is.null(ifc) && nrow(ifc)) {
    ifc$family <- factor(unname(IF_LABELS[ifc$if_family]), levels = IF_LABELS)
    ifc$age_group <- factor(ifc$age_group, levels = c("<15", "15-64", "65+"))
    b <- ifc |>
      dplyr::filter(!is.na(family), is.finite(mdd)) |>
      dplyr::group_by(family, age_group, temperature_C) |>
      dplyr::summarise(med = median(mdd), lo = quantile(mdd, 0.25),
                       hi = quantile(mdd, 0.75), .groups = "drop")
    ggplot(b, aes(temperature_C, med, color = family, fill = family)) +
      geom_ribbon(aes(ymin = lo, ymax = hi), alpha = 0.15, color = NA) +
      geom_line(linewidth = 0.85) +
      facet_wrap(~age_group, nrow = 1, scales = "free_y") +
      scale_y_continuous(labels = scales::label_scientific(digits = 1)) +
      scale_color_manual(values = IF_COLORS, name = "Exposure-response function") +
      scale_fill_manual(values = IF_COLORS, guide = "none") +
      labs(tag = "a",
           x = "Daily mean temperature (°C)",
           y = "Marginal deaths per degree") +
      theme_natcities() +
      guides(color = guide_legend(nrow = 2))
  } else patchwork::plot_spacer()

  # --- (b) what it does to baseline mortality --------------------------------
  dd <- gather_cities(cities, function(c) {
    d <- data.frame(family = names(IF_LABELS),
                    deaths = vapply(names(IF_LABELS),
                                    function(f) if_total_deaths(c, f), numeric(1)))
    if (all(is.na(d$deaths))) return(NULL)
    d
  })
  pb <- if (!is.null(dd) && nrow(dd)) {
    ref <- dd[dd$family == "masselot", c("city", "deaths")]
    names(ref)[2] <- "ref"
    d <- merge(dd, ref, by = "city")
    d <- d[is.finite(d$ref) & d$ref > 0 & d$family != "masselot", , drop = FALSE]
    d$ratio <- d$deaths / d$ref
    # How often the tail extension actually changes anything, computed rather
    # than asserted: it is identical to the main specification in most cities,
    # but not all, so "never binds" would be wrong.
    tl <- d[d$family == "masselot_tail", ]
    n_bind <- sum(abs(tl$ratio - 1) > 1e-9, na.rm = TRUE)
    max_bind <- if (n_bind) max(100 * abs(tl$ratio - 1), na.rm = TRUE) else 0
    d$family <- factor(unname(IF_LABELS[d$family]), levels = IF_LABELS)
    d <- attach_meta(d, meta)
    ggplot(d[is.finite(d$ratio) & d$ratio > 0, ], aes(family, ratio)) +
      geom_hline(yintercept = 1, linetype = 2, color = "grey55") +
      geom_boxplot(outlier.shape = NA, width = 0.5, fill = "grey95", color = "grey55") +
      geom_jitter(aes(color = climate_cluster), width = 0.13, height = 0,
                  size = 1.9, alpha = 0.75) +
      cluster_scale() +
      scale_y_log10(labels = scales::label_number(accuracy = 0.1, suffix = "x")) +
      labs(tag = "b",
           x = NULL, y = "Baseline heat deaths (ratio, log)") +
      theme_natcities() +
      theme(axis.text.x = element_text(angle = 15, hjust = 1))
  } else patchwork::plot_spacer()

  # Caption number (panel titles/subtitles removed -- Nature style).
  if (exists("n_bind"))
  message(sprintf(paste0("  [caption] the masselot tail extension binds in %d of ",
                         "%d cities (max %.1f%%)"), n_bind, nrow(tl), max_bind))

  fig <- (pa | pb) + patchwork::plot_layout(widths = c(2.1, 1)) &
    theme(legend.position = "bottom")
  save_item(fig, "si2_if_comparison", width = 14, height = 5.2)
}

if (!exists(".NATCITIES_NORUN")) build_si2()
