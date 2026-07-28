# =============================================================================
# exemplars.R  --  detailed single-city dashboards for cluster exemplars
# Renders exemplar_<city>.{pdf,png}: one rich dashboard per representative city
# (the cluster medoid from 00_city_meta.R). These carry the city-specific detail
# that is deliberately kept out of the cross-city summary figures.
#
#   (a) baseline heat mortality trajectory 2020-2050
#   (b) avoided deaths (25y) by pathway
#   (c) district greening vs vulnerability (uniform vs equity)
#   (d) city cost-benefit frontier with constrained portfolios
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

render_exemplar <- function(city, meta) {
  cl <- as.character(meta$climate_cluster[meta$city == city])
  clab <- city_label(city)

  # (a) baseline trajectory
  bt <- read_city_csv(city, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", city),
                      quiet = TRUE)
  pa <- if (!is.null(bt)) {
    ggplot(bt, aes(year, deaths_overall)) +
      geom_line(linewidth = 1, color = "grey30") + geom_point(size = 2, color = "grey30") +
      labs(tag = "a", title = "Baseline heat mortality", x = NULL, y = "Deaths / year") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # (b) avoided deaths by pathway
  keep <- c("Trees (uniform)", "AC (NET)", "EWS (marginal)")
  eff <- read_city_csv(city, sprintf("%s_policy_effectiveness.csv", city))
  pb <- if (!is.null(eff)) {
    e <- eff[eff$policy %in% keep, ]
    e$pathway <- factor(pathway_key(e$policy), levels = c("Trees", "AC", "EWS"))
    ggplot(e, aes(pathway, avoided_deaths_25y, fill = pathway)) +
      geom_col(width = 0.65) +
      scale_fill_manual(values = PATHWAY_COLORS, guide = "none") +
      labs(tag = "b", title = "Avoided deaths (25y) by pathway",
           x = NULL, y = "Avoided deaths") +
      theme_natcities()
  } else patchwork::plot_spacer()

  # (c) greening vs SVI
  pcm <- read_city_csv(city, sprintf("%s_policy_comparison.csv", city))
  pc <- if (!is.null(pcm) && all(c("SVI", "ΔGVI_uniform", "ΔGVI_equity") %in% names(pcm))) {
    long <- tidyr::pivot_longer(pcm[, c("SVI", "ΔGVI_uniform", "ΔGVI_equity", "population")],
                                c("ΔGVI_uniform", "ΔGVI_equity"),
                                names_to = "alloc", values_to = "dGVI")
    long$alloc <- factor(ifelse(grepl("equity", long$alloc), "Equity-targeted", "Uniform"),
                         levels = c("Uniform", "Equity-targeted"))
    ggplot(long, aes(SVI, dGVI, color = alloc)) +
      geom_point(aes(size = population), alpha = 0.75) +
      geom_smooth(method = "lm", se = FALSE, linewidth = 0.8, formula = y ~ x) +
      scale_color_manual(values = c("Uniform" = "#9E9E9E", "Equity-targeted" = "#2E7D32"),
                         name = "Allocation") +
      scale_size_continuous(guide = "none") +
      labs(tag = "c", title = "District greening vs vulnerability",
           x = "Social Vulnerability Index", y = expression(Delta*"GVI (points)")) +
      theme_natcities()
  } else patchwork::plot_spacer()

  # (d) frontier + portfolios
  bs <- read_city_csv(city, sprintf("%s_budget_sensitivity.csv", city))
  po <- read_city_csv(city, sprintf("%s_policy_constraints.csv", city))
  pd <- if (!is.null(bs)) {
    p <- ggplot(bs, aes(max_cost / 1e6, max_benefit)) +
      geom_line(color = PATHWAY_COLORS["Portfolio"], linewidth = 1) +
      geom_point(color = PATHWAY_COLORS["Portfolio"], size = 1.6)
    if (!is.null(po)) {
      po <- dplyr::distinct(po, scenario, .keep_all = TRUE)
      p <- p + geom_point(data = po, aes(cost / 1e6, benefit, shape = scenario),
                          size = 2.6, color = "grey15") +
        scale_shape_manual(values = 15:19, name = "Portfolio")
    }
    p + labs(tag = "d", title = "Cost-benefit frontier",
             x = "PV cost (M€)", y = "Avoided deaths (25y)") +
      theme_natcities()
  } else patchwork::plot_spacer()

  fig <- (pa | pb) / (pc | pd) +
    patchwork::plot_annotation(
      title = sprintf("%s  --  exemplar of the %s cluster", clab, cl),
      theme = theme(plot.title = element_text(face = "bold", size = 14)))
  save_item(fig, sprintf("exemplar_%s", city), width = 12, height = 9)
}

build_exemplars <- function() {
  banner("Exemplar city dashboards")
  meta <- load_city_meta()
  if (is.null(meta)) return(invisible(NULL))
  # A city is renderable only if it has the core CBA/effectiveness results.
  has_results <- function(c)
    !is.null(read_city_csv(c, sprintf("%s_policy_effectiveness.csv", c), quiet = TRUE))
  renderable <- Filter(has_results, discover_cities(require_tables = TRUE))

  exemplars <- meta$city[meta$is_exemplar %in% TRUE]
  chosen <- intersect(exemplars, renderable)
  if (!length(chosen)) {
    message("  [note] cluster medoid(s) lack results yet; ",
            "rendering available cities as provisional exemplars.")
    chosen <- renderable
  }
  if (!length(chosen)) { message("  No renderable cities."); return(invisible(NULL)) }
  for (c in chosen) {
    message(sprintf("  rendering exemplar: %s", c))
    tryCatch(render_exemplar(c, meta),
             error = function(e) message("  [FAILED] ", c, ": ", conditionMessage(e)))
  }
}

if (!exists(".NATCITIES_NORUN")) build_exemplars()
