# =============================================================================
# fig3_synergies.R  --  Summary Fig 3 (fig:result3)
# Synergies and trade-offs between adaptation levers, aggregated by climate
# cluster (population-weighted). Every panel shows an effect that both helps AND
# hurts, or a lever whose value spills across sectors.
#
#   (a) AC mortality ledger: net lives saved vs lives ADDED BACK by AC's own
#       waste heat (the self-undermining side-effect). Stacked = gross benefit.
#   (b) Trees as a multi-sector investment: share of tree cost recovered through
#       electricity savings + avoided-CO2 value (co-benefit synergy).
#   (c) Public greening x private AC synergy: AC net lives saved standalone vs
#       with urban greening, which softens the waste-heat penalty.
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

CARBON_PRICE <- 100   # EUR / tonne CO2, for valuing the tree carbon co-benefit

build_fig3 <- function(cities = discover_cities()) {
  banner("Summary Fig 3: synergies and trade-offs")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) { message("Missing cities/metadata."); return(invisible(NULL)) }

  # --- per-city synergy quantities from the CBA summary ----------------------
  syn <- gather_cities(cities, function(c) {
    j <- read_city_json(c, sprintf("%s_cba_summary.json", c), quiet = TRUE)
    if (is.null(j)) return(NULL)
    fe <- j$vegetation_feedbacks$falchetta_electricity
    lam <- j$vegetation_feedbacks$lambda_y_waste_heat
    data.frame(
      gross      = j$benefits$ac$gross_25y,
      net        = j$benefits$ac$net_25y,
      pen        = j$benefits$ac$waste_heat_penalty_25y,
      net_trees  = j$benefits$ac_with_trees_interaction$net_25y,
      trees_pv   = j$costs$trees$pv_total_base,
      elec_pct   = fe$tree_cost_coverage_all_policy_ac_users_pct,
      co2_t      = fe$cum_co2_avoided_all_policy_ac_users_t_25y)
  })
  syn <- attach_meta(syn, meta)
  syn <- syn[!is.na(syn$pop_k) & !is.na(syn$climate_cluster), , drop = FALSE]
  if (is.null(syn) || !nrow(syn)) { message("No synergy data."); return(invisible(NULL)) }

  # --- population-weighted aggregation per cluster ---------------------------
  agg <- syn |>
    dplyr::group_by(climate_cluster) |>
    dplyr::summarise(
      pop_k     = sum(pop_k),
      gross100k = 100 * sum(gross) / pop_k,
      net100k   = 100 * sum(net)   / pop_k,
      pen100k   = 100 * sum(pen)   / pop_k,
      penrem_pct = 100 * (sum(net_trees) - sum(net)) / sum(pen),
      elec_pct  = 100 * sum(elec_pct / 100 * trees_pv) / sum(trees_pv),
      co2_pct   = 100 * sum(co2_t) * CARBON_PRICE / sum(trees_pv),
      .groups   = "drop")
  agg$climate_cluster <- factor(agg$climate_cluster, levels = CLUSTER_LEVELS)

  clab <- function() scale_x_discrete(limits = CLUSTER_LEVELS, drop = FALSE)

  # --- (a) AC mortality ledger: net vs lost to waste heat --------------------
  la <- tidyr::pivot_longer(agg, c("net100k", "pen100k"),
                            names_to = "part", values_to = "v")
  la$part <- factor(ifelse(la$part == "net100k",
                           "Net lives saved", "Lives added back by waste heat"),
                    levels = c("Lives added back by waste heat", "Net lives saved"))
  LEDGER <- c("Net lives saved" = "#1565C0",
              "Lives added back by waste heat" = "#B71C1C")
  pa <- ggplot(la, aes(climate_cluster, v, fill = part)) +
    geom_col(width = 0.62) +
    geom_text(data = agg, inherit.aes = FALSE,
              aes(climate_cluster, gross100k,
                  label = sprintf("%.0f%% lost", 100 * pen100k / gross100k)),
              vjust = -0.5, size = 3, color = "grey25") +
    scale_fill_manual(values = LEDGER, name = NULL,
                      breaks = c("Net lives saved", "Lives added back by waste heat")) +
    clab() +
    scale_y_continuous(expand = expansion(mult = c(0, 0.12))) +
    labs(tag = "a", title = "Air conditioning eats its own benefit",
         subtitle = "Gross indoor cooling, split into net vs waste-heat feedback",
         x = NULL, y = "Avoided heat deaths / 100k (25y)") +
    theme_natcities()

  # --- (b) trees: co-benefits recovering their cost --------------------------
  lb <- tidyr::pivot_longer(agg, c("elec_pct", "co2_pct"),
                            names_to = "part", values_to = "v")
  lb$part <- factor(ifelse(lb$part == "elec_pct",
                           "AC electricity savings", "Avoided-CO₂ value"),
                    levels = c("AC electricity savings", "Avoided-CO₂ value"))
  COBEN <- c("AC electricity savings" = "#2E7D32", "Avoided-CO₂ value" = "#00838F")
  pb <- ggplot(lb, aes(climate_cluster, v, fill = part)) +
    geom_col(width = 0.62) +
    scale_fill_manual(values = COBEN, name = NULL) +
    clab() + scale_y_continuous(expand = expansion(mult = c(0, 0.1))) +
    labs(tag = "b", title = "Trees are a multi-sector investment",
         subtitle = "Cross-sector co-benefits recover part of the greening cost",
         x = NULL, y = "% of tree PV cost recovered") +
    theme_natcities()

  # --- (c) greening x AC synergy: share of AC's penalty cancelled by trees ---
  pc <- ggplot(agg, aes(climate_cluster, penrem_pct, fill = climate_cluster)) +
    geom_col(width = 0.62) +
    geom_text(aes(label = sprintf("%.0f%%", penrem_pct)),
              vjust = -0.4, size = 3.4, color = "grey20") +
    scale_fill_manual(values = CLUSTER_COLORS, guide = "none", drop = FALSE) +
    clab() + scale_y_continuous(expand = expansion(mult = c(0, 0.12))) +
    labs(tag = "c", title = "Greening claws back AC's side-effect",
         subtitle = "Share of AC's waste-heat penalty cancelled by urban shade",
         x = NULL, y = "% of waste-heat penalty removed") +
    theme_natcities()

  fig <- (pa | pb | pc) &
    theme(legend.position = "bottom", legend.text = element_text(size = rel(0.8)),
          plot.subtitle = element_text(size = rel(0.82)))
  save_item(fig, "fig3_synergies", width = 13.5, height = 5.4)
}

if (!exists(".NATCITIES_NORUN")) build_fig3()
