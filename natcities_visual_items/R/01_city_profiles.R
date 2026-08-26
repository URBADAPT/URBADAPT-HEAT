# =============================================================================
# 01_city_profiles.R  --  standardised cross-city outcome profiles
#
# Builds the 40 x 15 matrix of adaptation-outcome variables that Fig 1 panel b
# displays, standardised so that quantities in deaths, per cent, euros per
# capita and euros per life can share one colour scale. The variable list is the
# one the manuscript specifies: baseline mortality per 100k, the three
# pathway-specific % reductions, the three avoided deaths per 100k, the three
# present-value costs per capita, the three costs per death, the pooled public
# cost share (carried as the private share, see below), and the AC waste-heat
# penalty as a share of AC gross benefit.
#
# Two transformations are applied before standardising, and both matter for
# reading the panel:
#   - the ten right-skewed variables (mortality, avoided deaths, all euro
#     amounts) are log10-transformed first, so their z is in log space;
#   - a cost per death that is undefined because the pathway avoids ~no deaths
#     (Madrid and Sevilla greening) is censored at the sample maximum.
#
# Output: tables/city_outcome_profiles.csv (raw values + z_ columns).
#
# NO CITY TYPOLOGY IS PRODUCED. k-means clustering of these profiles was tested
# and abandoned: no variable set gave a defensible partition (best mean
# silhouette 0.32 of four candidate sets; the sets independent of the climate
# gradient were dominated by greening-coefficient coverage). The clustering code
# is retained below as an opt-in diagnostic, `cluster_diagnostics()`, so the
# claim stays reproducible, but the build never calls it and no archetype
# column is written. See the README for the full comparison table.
#
# Run under PowerShell (see 00_city_meta.R), after 00_city_meta.R:
#   Rscript 01_city_profiles.R
# =============================================================================

if (!exists("REPO_ROOT")) {
  .a <- commandArgs(trailingOnly = FALSE)
  .f <- sub("^--file=", "", .a[grep("^--file=", .a)])
  .d <- if (length(.f)) dirname(normalizePath(.f)) else getwd()
  source(file.path(.d, "_helpers.R"))
}

PROFILE_OUT     <- file.path(TAB_DIR, "city_outcome_profiles.csv")
CLUSTER_K_RANGE <- 2:6      # k chosen within this range by mean silhouette width
CLUSTER_SEED    <- 42

# ---- the clustering variables ------------------------------------------------
# `key`   column name in the feature table
# `label` short axis label for the fig1b heatmap
# `group` heatmap column grouping
# `log`   TRUE for the heavily right-skewed quantities (mortality, avoided
#         deaths, euros): three orders of magnitude across 40 cities, so an
#         unlogged z-score would be one city at +6 and 39 in a blob at -0.2.
# `worse_high` gives each variable a direction, so the heatmap can be drawn on a
# single "red = less favourable" convention. Without it the same red means a
# bigger benefit in one column and a bigger cost in the next, which is how a
# reader ends up concluding the opposite of the data.
#
# The burden variable is carried as the PRIVATE share rather than the public one
# precisely so that it has an unambiguous direction: a larger private share is
# the paper's equity concern (cooling costs landing on the households least able
# to bear them), whereas "high public share" is good or bad depending on the lens.
PROFILE_FEATURES <- dplyr::tribble(
  ~key,             ~label,               ~group,         ~log,  ~worse_high,
  "mort_100k",      "Baseline deaths",    "Risk",          TRUE,   TRUE,
  "red_Trees",      "Trees",              "% reduction",   FALSE,  FALSE,
  "red_AC",         "AC",                 "% reduction",   FALSE,  FALSE,
  "red_EWS",        "EWS",                "% reduction",   FALSE,  FALSE,
  "av100k_Trees",   "Trees",              "Avoided/100k",  TRUE,   FALSE,
  "av100k_AC",      "AC",                 "Avoided/100k",  TRUE,   FALSE,
  "av100k_EWS",     "EWS",                "Avoided/100k",  TRUE,   FALSE,
  "pvcap_Trees",    "Trees",              "Cost/capita",   TRUE,   TRUE,
  "pvcap_AC",       "AC",                 "Cost/capita",   TRUE,   TRUE,
  "pvcap_EWS",      "EWS",                "Cost/capita",   TRUE,   TRUE,
  "cpd_Trees",      "Trees",              "Cost/death",    TRUE,   TRUE,
  "cpd_AC",         "AC",                 "Cost/death",    TRUE,   TRUE,
  "cpd_EWS",        "EWS",                "Cost/death",    TRUE,   TRUE,
  "private_share",  "Private cost share", "Burden",        FALSE,  TRUE,
  "ac_penalty_pct", "AC waste heat",      "Burden",        FALSE,  TRUE)

PROFILE_GROUP_LEVELS <- c("Risk", "% reduction", "Avoided/100k", "Cost/capita",
                       "Cost/death", "Burden")

# The four tree-pathway variables, dropped in the sensitivity rerun.
PROFILE_TREE_KEYS <- c("red_Trees", "av100k_Trees", "pvcap_Trees", "cpd_Trees")

# External descriptors: written to the output and used by fig1b as annotations,
# never fed to kmeans.
PROFILE_ANNOT <- c(coef_lst_coverage_pct = "Coef. coverage",
                lcz_compact_pct       = "Compact built",
                lcz_built_pct         = "Built share",
                lcz_diversity         = "LCZ diversity")

# ---- feature assembly -------------------------------------------------------

profile_features <- function(cities = discover_cities(), meta = load_city_meta()) {
  feat <- gather_cities(cities, function(c) {
    # baseline mortality (2020, current-AC baseline)
    b <- read_city_csv(c, sprintf("annual_heat_deaths_baseline_current_ac_%s.csv", c),
                       quiet = TRUE)
    base <- if (!is.null(b) && "deaths_overall" %in% names(b))
      n1(b$deaths_overall[b$year == min(b$year)][1]) else NA_real_

    # pathway effectiveness: % reduction + avoided deaths (AC net of waste heat)
    e <- canonical_effectiveness(c)
    # cost-effectiveness: PV cost and euros per death, uniform labels
    k <- read_cea(c)
    if (is.null(e) || is.null(k)) return(NULL)

    # Pivot a pathway-keyed column to one column per pathway, in fixed order, so
    # a city missing a pathway yields NA in that slot rather than a shifted row.
    wide <- function(df, from, to) {
      x <- setNames(suppressWarnings(as.numeric(df[[from]])), df$pathway)
      setNames(as.list(unname(x[PATHWAY_LEVELS])), paste0(to, PATHWAY_LEVELS))
    }
    row <- c(list(deaths_2020 = base),
             wide(e, "pct_reduction", "red_"),
             wide(e, "avoided_deaths_25y", "avd_"),
             wide(k, "PV_Cost_EUR", "pv_"),
             wide(k, "cost_per_death", "cpd_"))

    # public/private division, pooled over the three pathways: the single
    # number the manuscript's "division of costs between public and private
    # actors" refers to. Trees and EWS are wholly public, so this is driven by
    # how large AC's private bill is relative to the rest of the portfolio.
    pp <- read_city_csv(c, sprintf("%s_public_private_costs.csv", c), quiet = TRUE)
    row$private_share <- if (!is.null(pp) && all(c("private_pv", "total_pv") %in% names(pp)))
      n1(sum(pp$private_pv, na.rm = TRUE) / sum(pp$total_pv, na.rm = TRUE)) else NA_real_

    # AC waste-heat penalty as a share of AC's own gross benefit: how much of
    # the cooling AC buys its own rejected heat takes back.
    j <- read_cba(c)
    row$ac_penalty_pct <- if (!is.null(j) && isTRUE(is.finite(j$ac_gross)) &&
                              isTRUE(j$ac_gross > 0))
      100 * j$ac_penalty / j$ac_gross else NA_real_

    as.data.frame(row, stringsAsFactors = FALSE)
  })
  if (is.null(feat)) return(NULL)

  feat <- attach_meta(feat, meta)
  feat <- feat[!is.na(feat$pop_k) & feat$pop_k > 0, , drop = FALSE]
  pop <- feat$pop_k * 1000

  feat$mort_100k <- per_100k(feat$deaths_2020, feat$pop_k)
  for (pw in PATHWAY_LEVELS) {
    feat[[paste0("av100k_", pw)]] <- per_100k(feat[[paste0("avd_", pw)]], feat$pop_k)
    feat[[paste0("pvcap_",  pw)]] <- feat[[paste0("pv_", pw)]] / pop
  }
  feat
}

# ---- matrix preparation -----------------------------------------------------

# Censored-high imputation for cost per death: NA means "avoids no deaths", i.e.
# worse than the most expensive city, so it takes that city's value rather than
# dropping the city from kmeans. read_cea() already blanked the 1e10-euro
# artefacts these cells would otherwise carry.
.impute_censored <- function(x) {
  bad <- !is.finite(x)
  if (any(bad) && any(!bad)) x[bad] <- max(x[!bad])
  x
}

# Log transform that survives the zero end (sevilla/madrid trees avoid ~0
# deaths): shift by half the smallest strictly positive value in the column.
.safe_log10 <- function(x) {
  pos <- x[is.finite(x) & x > 0]
  eps <- if (length(pos)) min(pos) / 2 else 1e-6
  log10(pmax(x, 0) + eps)
}

profile_matrix <- function(feat, keys = PROFILE_FEATURES$key) {
  spec <- PROFILE_FEATURES[PROFILE_FEATURES$key %in% keys, , drop = FALSE]
  M <- vapply(seq_len(nrow(spec)), function(i) {
    v <- suppressWarnings(as.numeric(feat[[spec$key[i]]]))
    if (grepl("^cpd_", spec$key[i])) v <- .impute_censored(v)
    if (spec$log[i]) v <- .safe_log10(v)
    # Any residual gap (a city missing one input) goes to the column mean, so
    # the city keeps its other variables instead of dropping out entirely.
    if (any(!is.finite(v))) v[!is.finite(v)] <- mean(v[is.finite(v)])
    v
  }, numeric(nrow(feat)))
  if (!is.matrix(M)) M <- matrix(M, nrow = nrow(feat))
  dimnames(M) <- list(feat$city, spec$key)
  scale(M)
}

# =============================================================================
# OPT-IN DIAGNOSTICS ONLY -- nothing below is called by the build or by any
# figure. Kept so the "no defensible typology" claim in the Fig 1 caption and
# the README can be re-derived: cluster_diagnostics() reprints the silhouettes,
# the climate concordance and the tree-variable sensitivity.
# =============================================================================

# ---- k selection ------------------------------------------------------------

# Mean silhouette width, implemented here rather than pulling in `cluster` for
# one function. Higher is better; k is chosen by the maximum over CLUSTER_K_RANGE.
.silhouette_mean <- function(D, cl) {
  D <- as.matrix(D); n <- nrow(D); s <- numeric(n)
  for (i in seq_len(n)) {
    own <- which(cl == cl[i])
    if (length(own) <= 1) { s[i] <- 0; next }
    a <- mean(D[i, setdiff(own, i)])
    b <- min(vapply(setdiff(unique(cl), cl[i]),
                    function(k) mean(D[i, cl == k]), numeric(1)))
    s[i] <- if (max(a, b) > 0) (b - a) / max(a, b) else 0
  }
  mean(s)
}

# Adjusted Rand index between two partitions: 1 = identical, ~0 = as similar as
# two random partitions. Used to report how much the tree variables matter.
.adjusted_rand <- function(a, b) {
  tab <- table(a, b); n <- sum(tab)
  cc <- function(x) sum(choose(x, 2))
  sij <- cc(as.vector(tab)); si <- cc(rowSums(tab)); sj <- cc(colSums(tab))
  expct <- si * sj / choose(n, 2)
  maxi  <- (si + sj) / 2
  if (isTRUE(all.equal(maxi, expct))) return(NA_real_)
  (sij - expct) / (maxi - expct)
}

.kmeans_fixed <- function(M, k) {
  set.seed(CLUSTER_SEED)
  kmeans(M, centers = k, nstart = 50, iter.max = 100)
}

pick_k <- function(M, ks = CLUSTER_K_RANGE) {
  ks <- ks[ks < nrow(M)]
  D <- dist(M)
  sil <- vapply(ks, function(k) .silhouette_mean(D, .kmeans_fixed(M, k)$cluster),
                numeric(1))
  names(sil) <- ks
  message("  silhouette by k: ",
          paste(sprintf("k=%d %.3f", ks, sil), collapse = "  "))
  list(k = ks[which.max(sil)], sil = sil)
}

# ---- naming -----------------------------------------------------------------

# Archetypes are ordered by descending baseline mortality and named A1..Ak, with
# an auto-generated descriptor from the two features furthest from the sample
# mean. The descriptor is a reading aid, not editorial copy: set
# ARCHETYPE_LABELS below to the names used in the manuscript and they win.
ARCHETYPE_LABELS <- character(0)   # e.g. c(A1 = "Hot, cooling-dependent")

.FEATURE_PHRASE <- c(
  mort_100k = "baseline risk", red_Trees = "greening effect",
  red_AC = "AC effect", red_EWS = "warning effect",
  av100k_Trees = "greening yield", av100k_AC = "AC yield",
  av100k_EWS = "warning yield", pvcap_Trees = "greening spend",
  pvcap_AC = "AC spend", pvcap_EWS = "warning spend",
  cpd_Trees = "greening cost per life", cpd_AC = "AC cost per life",
  cpd_EWS = "warning cost per life", private_share = "private burden",
  ac_penalty_pct = "waste-heat penalty")

.auto_descriptor <- function(centroid, n_terms = 2) {
  o <- order(abs(centroid), decreasing = TRUE)[seq_len(min(n_terms, length(centroid)))]
  parts <- vapply(o, function(i) {
    nm <- unname(.FEATURE_PHRASE[names(centroid)[i]])
    if (is.na(nm)) nm <- names(centroid)[i]
    paste(if (centroid[i] > 0) "high" else "low", nm)
  }, character(1))
  paste(parts, collapse = ", ")
}

# ---- diagnostics ------------------------------------------------------------

# Does an outcome-based archetype actually say anything a climate class does
# not? The manuscript's variable list is dominated by SCALE: six of the fifteen
# variables (baseline mortality, the three avoided-deaths-per-100k terms, and
# the cost-per-death terms that are their reciprocal) are close to monotone in
# baseline risk, which is itself close to monotone in warm-season temperature.
# So the partition can come out as a relabelled warm/cool split. This block
# prints the evidence on every run instead of leaving it to be rediscovered:
#   - variance share and temperature correlation of the leading outcome axis
#   - the archetype x climate-class contingency table and its concordance
.archetype_diagnostics <- function(feat, M) {
  p <- prcomp(M)
  vs <- 100 * p$sdev^2 / sum(p$sdev^2)
  pc1 <- p$x[, 1]
  rho_t <- suppressWarnings(cor(pc1, feat$warmseason_mean_t2m,
                               method = "spearman", use = "complete.obs"))
  rho_m <- suppressWarnings(cor(pc1, feat$mort_100k,
                               method = "spearman", use = "complete.obs"))
  rho_c <- suppressWarnings(cor(pc1, feat$coef_lst_coverage_pct,
                               method = "spearman", use = "complete.obs"))
  message(sprintf(paste0(
    "  [diagnostic] leading outcome axis carries %.0f%% of the variance and is",
    " essentially\n               a risk-magnitude axis: Spearman %+.2f with",
    " baseline mortality,\n               %+.2f with warm-season T2M, %+.2f",
    " with greening-coefficient coverage."),
    vs[1], rho_m, rho_t, rho_c))
  tab <- table(archetype = feat$archetype, climate = feat$climate_cluster)
  # Best one-to-one reading of the table: how much of the sample a climate class
  # already places in the right archetype.
  conc <- sum(apply(tab, 2, max)) / sum(tab)
  message(sprintf(paste0(
    "  [diagnostic] %.0f%% of cities are placed by their climate class alone,",
    " so these\n               archetypes largely restate the climate gradient",
    " rather than adding\n               a second, independent typology:"),
    100 * conc))
  print(tab)
  invisible(list(pc_var = vs, concordance = conc))
}

# ---- the build --------------------------------------------------------------

build_city_profiles <- function(cities = discover_cities()) {
  banner("City outcome profiles (standardised)")
  meta <- load_city_meta()
  if (!length(cities) || is.null(meta)) {
    message("Missing cities/metadata."); return(invisible(NULL)) }

  feat <- profile_features(cities, meta)
  if (is.null(feat) || nrow(feat) < 4) {
    message(sprintf("Only %d city(ies) with a complete outcome profile; need >= 4.",
                    if (is.null(feat)) 0 else nrow(feat)))
    return(invisible(NULL))
  }
  n_cens <- sum(vapply(paste0("cpd_", PATHWAY_LEVELS),
                       function(k) sum(!is.finite(feat[[k]])), integer(1)))
  message(sprintf("  %d cities x %d outcome variables%s", nrow(feat),
                  nrow(PROFILE_FEATURES),
                  if (n_cens) sprintf("; %d cost-per-death cell(s) censored high",
                                      n_cens) else ""))

  M <- profile_matrix(feat)
  Z <- as.data.frame(M)
  names(Z) <- paste0("z_", colnames(M))
  Z$city <- rownames(M)
  out <- dplyr::left_join(feat, Z, by = "city")

  keep_cols <- c("city", "city_label", "country", "pop_k", "warmseason_mean_t2m",
                 "climate_cluster", names(PROFILE_ANNOT), PROFILE_FEATURES$key,
                 paste0("z_", PROFILE_FEATURES$key))
  out <- out[order(-out$mort_100k), intersect(keep_cols, names(out)), drop = FALSE]
  readr::write_csv(out, PROFILE_OUT)
  message(sprintf("  [saved] city_outcome_profiles.csv (%d cities)", nrow(out)))
  invisible(out)
}

# Re-derive the evidence that no city typology is defensible. Not called by the
# build; run by hand as cluster_diagnostics() after 01_city_profiles.R.
cluster_diagnostics <- function(cities = discover_cities()) {
  banner("Clustering diagnostics (opt-in; no typology is published)")
  feat <- profile_features(cities, load_city_meta())
  if (is.null(feat) || nrow(feat) < 4) { message("Too few cities."); return(invisible(NULL)) }
  M   <- profile_matrix(feat)
  sel <- pick_k(M)
  km  <- .kmeans_fixed(M, sel$k)
  message(sprintf("  k = %d (best mean silhouette %.3f)", sel$k, max(sel$sil)))
  ord   <- order(tapply(feat$mort_100k, km$cluster, mean, na.rm = TRUE), decreasing = TRUE)
  remap <- setNames(sprintf("A%d", seq_along(ord)), sort(unique(km$cluster))[ord])
  feat$archetype <- unname(remap[as.character(km$cluster)])
  .archetype_diagnostics(feat, M)
  keep  <- setdiff(PROFILE_FEATURES$key, PROFILE_TREE_KEYS)
  km_nt <- .kmeans_fixed(profile_matrix(feat, keep), sel$k)
  message(sprintf(paste0("  [greening caveat] dropping the %d tree variables gives ",
                         "ARI %.2f"), length(PROFILE_TREE_KEYS),
                  .adjusted_rand(km$cluster, km_nt$cluster)))
  invisible(feat)
}

# Cached reader for the figure scripts.
load_city_profiles <- function() {
  if (!file.exists(PROFILE_OUT)) {
    message("city_outcome_profiles.csv not found -- run 01_city_profiles.R first.")
    return(NULL)
  }
  a <- suppressWarnings(readr::read_csv(PROFILE_OUT, show_col_types = FALSE))
  a$climate_cluster <- factor(a$climate_cluster, levels = CLUSTER_LEVELS)
  a
}

if (!exists(".NATCITIES_NORUN")) build_city_profiles()
